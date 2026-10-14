(function () {
    "use strict";
    const plugin = window.Asc.plugin;
    const $ = id => document.getElementById(id);
    plugin.init = function () {
        plugin.attachEvent("state", state => {
            for (const [id, values] of Object.entries(state.controls)) {
                const element = $(id);
                if (!element) continue;
                for (const [property, value] of Object.entries(values)) {
                    if (property !== "value" || element !== document.activeElement) element[property] = value;
                }
            }
            $("status").textContent = state.status;
            $("target").textContent = state.target;
        });
        const send = id => {
            const values = {};
            for (const field of ["title", "format", "copy-directory", "bridge-url", "bridge-token"]) values[field] = $(field).value;
            plugin.sendToPlugin("action", {id, values, checked: $("enabled").checked});
        };
        $("enabled").addEventListener("change", () => send("enabled"));
        for (const id of ["save-now", "connect", "choose-directory"]) $(id).addEventListener("click", () => send(id));
        plugin.sendToPlugin("ready", {});
    };
    plugin.button = function () { plugin.executeMethod("CloseWindow", [plugin.windowID]); };
})();
