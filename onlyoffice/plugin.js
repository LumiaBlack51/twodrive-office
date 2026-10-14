(function () {
    "use strict";
    const plugin = window.Asc.plugin;
    const $ = id => document.getElementById(id);
    let session = null, engine = null, polling = null, ticking = null;
    let connecting = false, bridgeConnecting = false;
    let controlPanel = null, autoAttempted = false, disposed = false;
    let snapshotRevision = 0;
    const snapshots = new WeakMap();
    const documentId = crypto.randomUUID ? crypto.randomUUID() : Date.now() + "-" + Math.random();
    let bridge = window.TWODRIVE_BRIDGE;
    const report = message => { $("status").textContent = message; publish(); };
    const sourcePath = desktop => desktop.LocalFileGetSaved() ? desktop.LocalFileGetSourcePath() : "";
    const fields = ["enabled", "source", "confirm-source", "title", "format", "copy-directory", "bridge-url", "bridge-token", "choose-source", "choose-directory", "connect", "save-now"];
    function publish() {
        if (!controlPanel) return;
        const controls = {};
        for (const id of fields) {
            const element = $(id);
            controls[id] = {value: element.value, checked: element.checked, disabled: element.disabled, readOnly: element.readOnly, hidden: element.hidden};
        }
        controlPanel.command("state", {controls, status: $("status").textContent, target: $("target").textContent});
    }
    function showPanel() {
        if (controlPanel) controlPanel.close();
        controlPanel = new window.Asc.PluginWindow();
        controlPanel.attachEvent("ready", publish);
        controlPanel.attachEvent("action", data => {
            if (!data || !fields.includes(data.id)) return;
            for (const id of ["title", "format", "copy-directory", "bridge-url", "bridge-token"]) {
                if (!$(id).disabled && data.values && typeof data.values[id] === "string") $(id).value = data.values[id];
            }
            const element = $(data.id);
            if (element.disabled) return;
            if (data.id === "enabled") {
                element.checked = data.checked === true;
                element.dispatchEvent(new Event("change"));
            } else if (["save-now", "connect", "choose-directory"].includes(data.id)) element.click();
            publish();
        });
        controlPanel.show({url: "panel.html", description: "TwoDrive 自动保存", type: "panel", isVisual: true, isInsideMode: true, isActivated: true, EditorsSupport: ["word", "cell", "slide"]});
    }
    function documentModified() {
        try { return window.parent.Asc.editor.isDocumentModified(); } catch (_) { return false; }
    }
    function changed() {
        if (session && (!session.in_place || documentModified())) engine.changed();
    }

    function validateBridge(url) {
        const parsed = new URL(url);
        if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1" || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash)
            throw new Error("服务地址必须是 http://127.0.0.1:端口");
        return parsed.origin;
    }
    async function request(path, options = {}) {
        const response = await fetch(validateBridge(bridge.url) + path, {
            ...options,
            headers: {Authorization: "Bearer " + bridge.token, ...(options.headers || {})},
            signal: AbortSignal.timeout(path.startsWith("/pick-") ? 185000 : 60000)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "本地服务连接失败");
        return data;
    }
    function jsonPost(path, data) {
        return request(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
    }
    async function exportedBytes(format) {
        // Desktop's file frames share the native SDK. This adapter is capability checked;
        // browser-hosted ONLYOFFICE Docs is deliberately unsupported.
        let editorWindow, editor;
        try {
            editorWindow = window.parent;
            editor = editorWindow.Asc && editorWindow.Asc.editor;
        } catch (_) { throw new Error("当前编辑器不允许桌面快照接口，请使用受支持的 Desktop Editors 版本"); }
        if (!editor || typeof editor.asc_nativeGetFile !== "function") throw new Error("此版本没有桌面快照接口");
        const desktop = editorWindow.AscDesktopEditor;
        const source = sourcePath(desktop);
        if (source && desktop.isFileCrypt(source)) throw new Error("加密文档暂不支持自动保存，请手动保存");
        // A retry can export newer content even without an editor change event.
        // Give every attempt a new bridge version, including lost-reply retries.
        const snapshot = {binary: editor.asc_nativeGetFile(), media: {}, source_path: source, revision: ++snapshotRevision};
        if (snapshot.binary.length > 128 * 1024 * 1024) throw new Error("文档快照超过 128 MiB");
        // Closure compilation renames the registry symbol. Identify its desktop media root
        // structurally, rather than hard-coding a minified name from one editor build.
        const roots = [];
        for (const value of Object.values(editorWindow.AscCommon)) {
            if (!value || typeof value !== "object" || !value.urls || typeof value.urls !== "object") continue;
            for (const field of Object.values(value)) {
                if (typeof field === "string" && /^file:\/\/.*\/recover\//.test(field)) roots.push(field.replace(/\/$/, ""));
            }
        }
        const binary = Uint8Array.from(atob(snapshot.binary.split(";").slice(3).join(";")), c => c.charCodeAt(0));
        const names = new Set();
        const pattern = /\b([a-zA-Z0-9_.-]{1,128}\.(?:png|jpe?g|gif|bmp|svg|emf|wmf|tiff?|webp|bin|mp4|wav|mp3|xlsx))\b/gi;
        // Native resources are encoded as UTF-16 strings; a field may start at either alignment.
        for (const offset of [0, 1]) {
            const text = new TextDecoder("utf-16le").decode(binary.subarray(offset));
            for (const match of text.matchAll(pattern)) names.add(match[1]);
        }
        if (names.size && new Set(roots).size !== 1) throw new Error("无法安全识别当前文档的媒体目录，自动保存已停止");
        if (names.size > 5000) throw new Error("文档媒体数量过多");
        for (const name of names) {
            const encoded = desktop.GetImageBase64(roots[0] + "/media/" + name, true);
            // Text can mention a file name. Missing candidates are harmless; the bridge checks
            // all actual package relationships and refuses to publish missing resources.
            if (!encoded) continue;
            snapshot.media[name] = encoded;
        }
        if (snapshot.source_path !== session.source_path) throw new Error("文档已另存为新文件，请重新开启自动保存");
        const bytes = new TextEncoder().encode(JSON.stringify(snapshot)).buffer;
        if (bytes.byteLength > 128 * 1024 * 1024) throw new Error("导出文件超过 128 MiB");
        snapshots.set(bytes, {binary: snapshot.binary, session});
        return bytes;
    }
    function didSave(bytes, result) {
        const snapshot = snapshots.get(bytes);
        if (!snapshot || snapshot.session !== session || !result || result.status !== "saved_local" || result.target !== session.target)
            throw new Error("未收到此版本的写入确认，修改仍待保存");
        if (!session.in_place) return "已保存到副本；原文档的保存状态未改变。云端同步由 TwoDrive 处理";
        if (sourcePath(window.parent.AscDesktopEditor) !== session.source_path)
            throw new Error("文档已另存为新文件，请重新开启自动保存");
        const state = window.TwoDriveNativeState.markSaved(window.parent, snapshot.binary);
        if (state === "pending") {
            engine.changed();
            return "上一版本已保存到原文件；新修改仍待保存";
        }
        if (state === "unsupported") return "已保存到原文件；此版本无法更新编辑器的未保存标记。云端同步由 TwoDrive 处理";
        return "已保存到原文件，编辑器状态已更新；云端同步由 TwoDrive 处理";
    }
    function lockInputs(locked) {
        ["title", "format", "copy-directory", "choose-directory", "connect"].forEach(id => { $(id).disabled = locked; });
        $("save-now").disabled = !locked;
        publish();
    }
    async function disable() {
        engine.stop();
        const old = session;
        session = null;
        lockInputs(false);
        report("自动保存：关");
        if (old) await request("/sessions/" + old.session_id, {method: "DELETE"}).catch(() => {});
    }
    async function toggle(automatic = false) {
        if (connecting) return;
        if (!$("enabled").checked) return disable();
        connecting = true;
        $("enabled").disabled = true;
        try {
            const source = sourcePath(window.parent.AscDesktopEditor);
            if (automatic && !source) {
                $("enabled").checked = false;
                report("未命名文档：自动保存未开启，可在面板选择副本目录后手动开启");
                return;
            }
            if (source !== $("source").value) {
                $("source").value = source;
                const extension = source.split(".").pop().toLowerCase();
                if (["docx", "xlsx", "pptx", "odt", "ods", "odp"].includes(extension)) $("format").value = extension;
            }
            if (source && !$("confirm-source").checked) throw new Error("请先确认原文件就是当前打开的文档");
            const opened = await jsonPost("/sessions", {
                source_path: source || null, title: $("title").value, document_id: documentId,
                extension: $("format").value, copy_directory: $("copy-directory").value.trim() || null,
                confirm_source: $("confirm-source").checked
            });
            if (disposed || (automatic && !opened.in_place)) {
                await request("/sessions/" + opened.session_id, {method: "DELETE", keepalive: true});
                $("enabled").checked = false;
                if (!disposed) report("文档在 TwoDrive 外：自动保存未开启，可在面板手动保存副本");
                return;
            }
            session = opened;
            session.source_path = source;
            $("target").textContent = (session.in_place ? "原位保存：" : "保存副本：") + session.target;
            lockInputs(true);
            const dirty = !automatic || documentModified();
            engine.start(dirty);
            report(dirty ? "自动保存：开，待保存" : "自动保存：开（TwoDrive 内文档），等待修改");
        } catch (error) {
            $("enabled").checked = false;
            report(error.message);
        } finally {
            connecting = false;
            $("enabled").disabled = false;
            publish();
        }
    }
    async function connect(automatic = false) {
        if (bridgeConnecting || disposed) return;
        bridgeConnecting = true;
        bridge = {url: $("bridge-url").value.trim(), token: $("bridge-token").value.trim()};
        try {
            const settings = await request("/settings");
            $("copy-directory").value = settings.copy_directory;
            report("已连接 TwoDrive，选择保存位置后开启自动保存");
            if (automatic && !autoAttempted && !disposed && !engine.enabled && !connecting) {
                // The bridge is authoritative about canonical paths, symlinks,
                // mount availability and writability. Never auto-start a copy.
                if (sourcePath(window.parent.AscDesktopEditor)) autoAttempted = true;
                $("enabled").checked = true;
                await toggle(true);
            }
        } catch (error) {
            report("本地服务未连接：" + error.message);
        } finally {
            bridgeConnecting = false;
            publish();
        }
    }
    plugin.init = function () {
        engine = new window.TwoDriveAutoSave.AutoSave(
            () => exportedBytes(session.extension),
            bytes => request("/sessions/" + session.session_id + "/snapshot", {method: "POST", headers: {"Content-Type": "application/json"}, body: bytes}),
            report, () => Date.now(), didSave
        );
        const formats = {word: "docx", cell: "xlsx", slide: "pptx"};
        $("format").value = formats[plugin.info.editorType] || "docx";
        $("source").readOnly = true;
        $("choose-source").hidden = true;
        $("confirm-source").disabled = true;
        let desktop;
        try { desktop = window.parent.AscDesktopEditor; } catch (_) { desktop = null; }
        if (desktop && typeof desktop.LocalFileGetSourcePath === "function") {
            const source = sourcePath(desktop);
            $("source").value = source;
            $("confirm-source").checked = true;
            const extension = source && source.split(".").pop().toLowerCase();
            if (["docx", "xlsx", "pptx", "odt", "ods", "odp"].includes(extension)) $("format").value = extension;
        } else {
            $("enabled").disabled = true;
            report("此插件需要 ONLYOFFICE Desktop Editors 的原生接口");
            return;
        }
        $("bridge-url").value = bridge.url;
        $("bridge-token").value = bridge.token;
        $("enabled").addEventListener("change", () => { autoAttempted = true; toggle(); });
        $("save-now").addEventListener("click", () => { engine.changed(); engine.tick(true); });
        $("connect").addEventListener("click", () => connect());
        $("choose-directory").addEventListener("click", async () => {
            try { const result = await jsonPost("/pick-directory", {}); if (result.path) $("copy-directory").value = result.path; publish(); }
            catch (error) { report(error.message); }
        });
        $("choose-source").addEventListener("click", async () => {
            try {
                const result = await jsonPost("/pick-source", {});
                if (result.path) {
                    $("source").value = result.path;
                    $("confirm-source").checked = false;
                    const extension = result.path.split(".").pop().toLowerCase();
                    if (["docx", "xlsx", "pptx", "odt", "ods", "odp"].includes(extension)) $("format").value = extension;
                }
            } catch (error) { report(error.message); }
        });
        plugin.attachEvent("onParagraphText", changed);
        plugin.attachEvent("onTargetPositionChanged", changed);
        plugin.attachEvent("onDocumentContentReady", () => { if (!autoAttempted) connect(true); });
        plugin.attachToolbarMenuClickEvent("twodrive-panel", showPanel);
        plugin.executeMethod("AddToolbarMenuItem", [{guid: plugin.guid, tabs: [{id: "twodrive", text: "TwoDrive", items: [{id: "twodrive-panel", type: "big-button", text: "自动保存", hint: "自动保存状态与设置", icons: "icon.svg", lockInViewMode: false}]}]}]);
        plugin.attachEvent("onChangeRestrictions", () => {
            if (engine.enabled) { $("enabled").checked = false; disable(); report("编辑权限改变，自动保存已停止，请检查后重新开启"); }
        });
        ticking = setInterval(() => engine.tick(), 250);
        polling = setInterval(() => { if (engine.enabled && !engine.busy && engine.dueAt === null) changed(); }, 9000);
        connect(true);
    };
    plugin.button = function () { if (controlPanel) { controlPanel.close(); controlPanel = null; } };
    window.addEventListener("beforeunload", () => {
        disposed = true;
        clearInterval(polling); clearInterval(ticking);
        if (engine) engine.stop();
        if (session) request("/sessions/" + session.session_id, {method: "DELETE", keepalive: true}).catch(() => {});
    });
})();
