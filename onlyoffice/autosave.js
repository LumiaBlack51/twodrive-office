(function (root) {
    "use strict";
    function equalBytes(a, b) {
        if (!a || a.byteLength !== b.byteLength) return false;
        const left = new Uint8Array(a), right = new Uint8Array(b);
        for (let i = 0; i < left.length; i++) if (left[i] !== right[i]) return false;
        return true;
    }
    class AutoSave {
        constructor(exportDocument, writeDocument, report, now = () => Date.now()) {
            this.exportDocument = exportDocument;
            this.writeDocument = writeDocument;
            this.report = report;
            this.now = now;
            this.enabled = this.busy = false;
            this.generation = this.revision = this.savedRevision = 0;
            this.firstChange = this.dueAt = null;
            this.failures = 0;
            this.retryAt = 0;
            this.lastBytes = null;
        }
        start() {
            this.generation++;
            this.enabled = true;
            this.lastBytes = null;
            this.failures = this.retryAt = 0;
            this.changed();
        }
        stop() {
            this.generation++;
            this.enabled = false;
            this.firstChange = this.dueAt = null;
        }
        changed() {
            if (!this.enabled) return;
            const now = this.now();
            this.revision++;
            if (this.firstChange === null) this.firstChange = now;
            this.dueAt = Math.min(now + 1000, this.firstChange + 5000);
        }
        async tick(force = false) {
            if (!this.enabled || this.busy) return;
            const now = this.now();
            if (!force && (this.dueAt === null || now < Math.max(this.dueAt, this.retryAt))) return;
            const generation = this.generation, revision = this.revision;
            this.busy = true;
            try {
                this.report("正在保存当前内容…");
                const bytes = await this.exportDocument();
                if (!this.enabled || generation !== this.generation) return;
                if (!(bytes instanceof ArrayBuffer) || !bytes.byteLength) throw new Error("编辑器返回的导出文件为空");
                if (!equalBytes(this.lastBytes, bytes)) await this.writeDocument(bytes);
                if (!this.enabled || generation !== this.generation) return;
                this.lastBytes = bytes;
                this.savedRevision = revision;
                this.failures = this.retryAt = 0;
                if (this.revision === revision) this.firstChange = this.dueAt = null;
                else { this.firstChange = this.now(); this.dueAt = this.now() + 1000; }
                this.report("已本地保存，云端由 TwoDrive 同步");
            } catch (error) {
                if (!this.enabled || generation !== this.generation) return;
                this.failures++;
                this.retryAt = this.now() + Math.min(30000, 1000 * (2 ** Math.min(this.failures, 5)));
                this.report("保存失败，修改仍待保存：" + error.message);
            } finally {
                this.busy = false;
            }
        }
    }
    root.TwoDriveAutoSave = {AutoSave, equalBytes};
    if (typeof module !== "undefined") module.exports = root.TwoDriveAutoSave;
})(typeof window === "undefined" ? globalThis : window);
