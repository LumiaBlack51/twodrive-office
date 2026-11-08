"""Wine COM snapshots. The Linux bridge owns persistence; Office owns native Save."""
from __future__ import annotations

import base64
import io
import os
import posixpath
import secrets
from pathlib import Path
import zipfile
from xml.dom import minidom
from xml.parsers.expat import ExpatError
import xml.etree.ElementTree as ET

from .policy import SaveError, check_source, fingerprint, normalized, prepare_target

PKG = 'http://schemas.microsoft.com/office/2006/xmlPackage'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
LIMIT = 128 * 1024 * 1024


def check_office_source(source):
    check_source(source)
    if source and source.is_file():
        with zipfile.ZipFile(source) as archive:
            if any(name.lower().startswith('_xmlsignatures/') for name in archive.namelist()):
                raise SaveError('已签名的 Office 文档不支持自动保存')


def resolve_wine_path(prefix, source):
    """Resolve the actual bottle mapping in Linux, including Wine case folding.

    Office's AppV layer can prevent its process from using Wine's Unix-name API.
    Never guess that Z: is / or persist an install-time mapping which can change.
    """
    if not isinstance(prefix, str) or not prefix or '\x00' in prefix:
        raise SaveError('Wine prefix 无效')
    prefix = Path(prefix).expanduser().resolve(strict=True)
    if (prefix.stat().st_uid != os.getuid() or not (prefix / 'user.reg').is_file()
            or not (prefix / 'drive_c').is_dir()):
        raise SaveError('Wine prefix 无效')
    if (not isinstance(source, str) or len(source) < 3 or not source[0].isascii()
            or not source[0].isalpha() or source[1:3] != ':\\' or '\x00' in source):
        raise SaveError('仅支持已有 Wine 盘符中的绝对文档路径')
    drive = prefix / 'dosdevices' / (source[0].lower() + ':')
    if not drive.is_symlink():
        raise SaveError('Wine 盘符映射不存在')
    target = drive.resolve(strict=True)
    for component in source[3:].replace('\\', '/').split('/'):
        if component in ('', '.'):
            continue
        if component == '..':
            raise SaveError('Wine 文档路径包含父目录跳转')
        exact = target / component
        if not exact.exists():
            matches = [p for p in target.iterdir() if p.name.casefold() == component.casefold()]
            if len(matches) != 1:
                raise SaveError('Wine 文档路径不存在或大小写有歧义')
            exact = matches[0]
        target = exact
    return str(target.resolve(strict=True))


def flat_opc(content):
    """Preserve original namespace prefixes, including mc:Ignorable bindings."""
    if len(content) > LIMIT or b'<!DOCTYPE' in content or b'<!ENTITY' in content:
        raise SaveError('Word XML 大小或实体声明不受支持')
    try:
        dom = minidom.parseString(content)
        if dom.documentElement.namespaceURI != PKG or dom.documentElement.localName != 'package':
            raise ValueError('not a Flat OPC package')
        types = ET.Element('Types', xmlns=CT)
        result, seen = io.BytesIO(), set()
        with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
            for part in dom.documentElement.childNodes:
                if part.nodeType != part.ELEMENT_NODE:
                    continue
                if part.namespaceURI != PKG or part.localName != 'part':
                    raise ValueError('unknown package element')
                name = part.getAttributeNS(PKG, 'name')
                mime = part.getAttributeNS(PKG, 'contentType')
                path = name.lstrip('/')
                if (not name.startswith('/') or not path or '\\' in path or '%' in path
                        or posixpath.normpath(path) != path or path.startswith('../')
                        or path in seen or path == '[Content_Types].xml' or not mime):
                    raise ValueError('invalid package part')
                seen.add(path)
                if path.lower().endswith('vbaproject.bin') or path.lower().startswith('_xmlsignatures/'):
                    raise SaveError('含宏或签名的文档不支持自动保存')
                children = [c for c in part.childNodes if c.nodeType == c.ELEMENT_NODE]
                if len(children) != 1 or children[0].namespaceURI != PKG:
                    raise ValueError('invalid part data')
                data = children[0]
                if data.localName == 'binaryData':
                    # Remove only XML whitespace; base64 validation rejects other bytes.
                    text = ''.join(c.data for c in data.childNodes if c.nodeType in (c.TEXT_NODE, c.CDATA_SECTION_NODE))
                    payload = base64.b64decode(''.join(text.split()), validate=True)
                elif data.localName == 'xmlData':
                    nodes = [c for c in data.childNodes if c.nodeType == c.ELEMENT_NODE]
                    if len(nodes) != 1:
                        raise ValueError('invalid XML part')
                    node = nodes[0].cloneNode(True)
                    ancestors, ancestor = [], nodes[0]
                    while ancestor and ancestor.nodeType == ancestor.ELEMENT_NODE:
                        ancestors.append(ancestor)
                        ancestor = ancestor.parentNode
                    bindings = {}
                    for ancestor in reversed(ancestors):
                        for attr in ancestor.attributes.values():
                            if attr.name == 'xmlns' or attr.name.startswith('xmlns:'):
                                bindings[attr.name] = attr.value
                    for name_, value in bindings.items():
                        if not node.hasAttribute(name_):
                            node.setAttribute(name_, value)
                    payload = node.toxml(encoding='utf-8')
                else:
                    raise ValueError('unknown part encoding')
                ET.SubElement(types, 'Override', PartName=name, ContentType=mime)
                archive.writestr(path, payload)
            archive.writestr('[Content_Types].xml', ET.tostring(types, encoding='utf-8', xml_declaration=True))
        dom.unlink()
        return result.getvalue()
    except (ValueError, ExpatError, UnicodeError) as exc:
        raise SaveError('Word 未返回完整的 Flat OPC 文档') from exc


def save_office(bridge, key, content, revision, source, encoding):
    if type(revision) is not int or not 1 <= revision <= 2**53:
        raise SaveError('Office 快照版本无效')
    session = bridge.sessions.get(key)
    if session is None or (normalized(source) if source else None) != session.source:
        raise SaveError('Office 快照源位置已改变，请重新开启')
    if session.plan.extension not in ('docx', 'xlsx', 'pptx'):
        raise SaveError('Wine 适配器仅支持 DOCX/XLSX/PPTX')
    check_office_source(session.source)
    if encoding == 'flatopc' and session.plan.extension == 'docx':
        content = flat_opc(content)
    elif encoding != 'ooxml':
        raise SaveError('Office 快照编码不受支持')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if any(p.lower().endswith('vbaproject.bin') or p.lower().startswith('_xmlsignatures/') for p in archive.namelist()):
                raise SaveError('含宏或签名的文档不支持自动保存')
    except zipfile.BadZipFile as exc:
        raise SaveError('Office 快照不完整') from exc
    return bridge.save(key, content, revision, defer=True)


def prepare_native(bridge, key, *, explicit=False):
    session = bridge.sessions.get(key)
    if session is None or not session.native_publish:
        raise SaveError('不是 Office 原位保存会话')
    with session.lock:
        check_office_source(session.source)
        if explicit:
            # Only the COM client's observed user Save event uses this path.
            prepare_target(bridge.settings, session.plan)
            check_source(session.source)
        else:
            bridge.validate_unchanged_target(session)
        session.native_token = secrets.token_urlsafe(24)
        session.native_revision = session.last_revision
        return {'status': 'native_save_ready', 'target': str(session.plan.target), 'native_token': session.native_token}


def complete_native(bridge, key, content, token):
    session = bridge.sessions.get(key)
    if session is None or not session.native_publish:
        raise SaveError('不是 Office 原位保存会话')
    with session.lock:
        if (not token or not secrets.compare_digest(token, session.native_token)
                or session.last_revision != session.native_revision):
            raise SaveError('原生保存确认已过期，本地版本已保留')
        prepare_target(bridge.settings, session.plan)
        check_office_source(session.source)
        before = fingerprint(session.plan.target)
        # An acknowledgement cannot discard a pending revision unless Office's
        # just-read native file is still exactly present at the guarded target.
        if before[2] != len(content) or session.plan.target.read_bytes() != content or fingerprint(session.plan.target) != before:
            raise SaveError('原生保存确认期间目标改变，本地版本已保留')
        from .bridge import validate_document
        validate_document(content, session.plan.extension)
        session.local.native_saved()
        session.native_token = ''
        session.content_digest = None
        return bridge.saved_result(session)
