(function (root) {
    "use strict";

    // Desktop 9.4 compiles private History fields to different names per editor.
    // Recognize the complete Reset_SavedIndex implementation, not a build's names.
    // Unknown SDKs keep their dirty flag; none of these private methods are invoked.
    const resetSignature = /^function\(([$\w]+)\)\{this\.([$\w]+)=null===this\.\2&&-1===this\.([$\w]+)\?null:this\.\3;!0===this\.([$\w]+)\?!0===\1&&\(this\.([$\w]+)=this\.\3,this\.([$\w]+)=!1\):this\.\6=!1;?\}$/;

    function presentationContent(binary) {
        // PPTY v1 has a 30-entry seek table. Only Core attribute 5 is an
        // export-generated clock (CCore.toStream), never slide text or media.
        // Unknown layouts fall back to exact comparison and keep the warning.
        if (typeof binary !== "string") return null;
        const header = /^PPTY;v1;([1-9]\d*);/.exec(binary);
        if (!header) return null;
        try {
            const data = Uint8Array.from(atob(binary.slice(header[0].length)), c => c.charCodeAt(0));
            if (data.length !== Number(header[1]) || data.length < 158) return null;
            const view = new DataView(data.buffer);
            const tables = [], types = new Set();
            let position = 0;
            for (; position < 150 && data[position] !== 0; position += 5) {
                const type = data[position], offset = view.getUint32(position + 1, true);
                if (types.has(type) || offset < 150 || offset >= data.length ||
                    (tables.length && offset <= tables.at(-1).offset)) return null;
                types.add(type); tables.push({type, offset});
            }
            for (; position < 150; position++) if (data[position] !== 0) return null;
            if (tables[0]?.type !== 255 || tables[0].offset !== 150 ||
                view.getUint32(150, true) !== 0x50505459 || view.getUint32(154, true) !== 0) return null;
            const core = tables.findIndex(table => table.type === 2);
            if (core < 0) return null;
            const start = tables[core].offset, limit = tables[core + 1]?.offset ?? data.length;
            if (start + 6 >= limit || data[start] !== 2 ||
                start + 5 + view.getUint32(start + 1, true) !== limit || data[start + 5] !== 250) return null;
            let cursor = start + 6, previous = -1, modified = null;
            while (cursor < limit && data[cursor] !== 251) {
                const attribute = data[cursor++];
                if (attribute <= previous || attribute > 5 || cursor + 4 > limit) return null;
                previous = attribute;
                const length = view.getUint32(cursor, true) * 2;
                cursor += 4;
                if (cursor + length >= limit) return null;
                if (attribute === 5) {
                    const date = new TextDecoder("utf-16le").decode(data.subarray(cursor, cursor + length));
                    if (length !== 40 || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(date)) return null;
                    modified = {start: cursor, end: cursor + length};
                }
                cursor += length;
            }
            if (cursor >= limit || data[cursor] !== 251 || !modified) return null;
            data.fill(0, modified.start, modified.end);
            return data;
        } catch (_) { return null; }
    }

    function sameSnapshot(left, right) {
        if (left === right) return true;
        const a = presentationContent(left), b = presentationContent(right);
        if (!a || !b || a.length !== b.length) return false;
        for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
        return true;
    }

    function savedFields(history) {
        if (!history || !Object.getPrototypeOf(history)) return null;
        const matches = [];
        for (const name of Object.getOwnPropertyNames(Object.getPrototypeOf(history))) {
            const method = history[name];
            if (typeof method !== "function") continue;
            const match = resetSignature.exec(Function.prototype.toString.call(method).replace(/\s+/g, ""));
            if (!match) continue;
            const [, , recovery, index, userMode, saved, force] = match;
            if (new Set([recovery, index, userMode, saved, force]).size !== 5) continue;
            if (![recovery, index, userMode, saved, force].every(key => Object.hasOwn(history, key))) continue;
            const validIndex = value => Number.isSafeInteger(value) && value >= -1;
            if (!validIndex(history[index]) || history[userMode] !== true || typeof history[force] !== "boolean") continue;
            if (![history[recovery], history[saved]].every(value => value === null || validIndex(value))) continue;
            matches.push({index, saved, force});
        }
        return matches.length === 1 ? matches[0] : null;
    }

    function markSaved(editorWindow, binary) {
        const editor = editorWindow.Asc && editorWindow.Asc.editor;
        const history = editorWindow.AscCommon && editorWindow.AscCommon.History;
        const fields = savedFields(history);
        if (!fields || !editor || typeof editor.SetDocumentModified !== "function" ||
            typeof editor.isDocumentModified !== "function" || typeof editor.asc_nativeGetFile !== "function") return "unsupported";

        // Honor the complete native guard: a save in flight, an embedded frame,
        // or an active cell edit must keep its warning even if the model matches.
        const modified = editor.asc_isDocumentModified || editor.isDocumentModified;
        const guard = /^function\(\)\{return!this\.([$\w]+)((?:\|\|this\.[$\w]+(?:\(\))?)*)\?(?:!0|true):/.exec(
            Function.prototype.toString.call(modified).replace(/\s+/g, ""));
        if (!guard || typeof editor[guard[1]] !== "boolean") return "unsupported";
        if (!editor[guard[1]]) return "pending";
        for (const blocker of guard[2].matchAll(/\|\|this\.([$\w]+)(\(\))?/g)) {
            const value = blocker[2] ? (typeof editor[blocker[1]] === "function" ? editor[blocker[1]]() : undefined) : editor[blocker[1]];
            if (typeof value !== "boolean") return "unsupported";
            if (value) return "pending";
        }
        if (!sameSnapshot(editor.asc_nativeGetFile(), binary)) {
            // A macro or a merged undo point can change content before Desktop
            // refreshes its dirty event. Keep an explicit warning for that edit.
            history[fields.force] = true;
            editor.SetDocumentModified(true);
            if (typeof editor._onUpdateDocumentCanSave === "function") editor._onUpdateDocumentCanSave();
            return "pending";
        }

        const previousSaved = history[fields.saved], previousForce = history[fields.force];
        // Only advance the user-visible saved point. The recovery/delta checkpoint
        // belongs to Desktop's own recovery pipeline and must remain untouched.
        history[fields.saved] = history[fields.index];
        history[fields.force] = false;
        try {
            editor.SetDocumentModified(false);
            if (typeof editor._onUpdateDocumentCanSave === "function") editor._onUpdateDocumentCanSave();
            if (!editor.isDocumentModified()) return "saved";
        } catch (_) {
            // Restore the warning if an SDK hook fails or an edit is still active.
        }
        history[fields.saved] = previousSaved;
        history[fields.force] = previousForce;
        editor.SetDocumentModified(true);
        return "pending";
    }

    root.TwoDriveNativeState = {markSaved, sameSnapshot};
    if (typeof module !== "undefined") module.exports = root.TwoDriveNativeState;
})(typeof window === "undefined" ? globalThis : window);
