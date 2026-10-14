(function (root) {
    "use strict";

    // Desktop 9.4 compiles private History fields to different names per editor.
    // Recognize the complete Reset_SavedIndex implementation, not a build's names.
    // Unknown SDKs keep their dirty flag; none of these private methods are invoked.
    const resetSignature = /^function\(([$\w]+)\)\{this\.([$\w]+)=null===this\.\2&&-1===this\.([$\w]+)\?null:this\.\3;!0===this\.([$\w]+)\?!0===\1&&\(this\.([$\w]+)=this\.\3,this\.([$\w]+)=!1\):this\.\6=!1;?\}$/;

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
        if (editor.asc_nativeGetFile() !== binary) {
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

    root.TwoDriveNativeState = {markSaved};
    if (typeof module !== "undefined") module.exports = root.TwoDriveNativeState;
})(typeof window === "undefined" ? globalThis : window);
