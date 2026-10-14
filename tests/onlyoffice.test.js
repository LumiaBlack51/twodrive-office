const test = require("node:test");
const assert = require("node:assert/strict");
const {AutoSave} = require("../onlyoffice/autosave.js");
const content = text => new TextEncoder().encode(text).buffer;

test("coalesces typing and saves latest export once, with maximum delay", async () => {
    let now = 0, writes = 0;
    const engine = new AutoSave(async () => content("current edit"), async () => writes++, () => {}, () => now);
    engine.start();
    for (now = 0; now < 5000; now += 500) { engine.changed(); await engine.tick(); }
    assert.equal(writes, 0);
    await engine.tick();
    assert.equal(writes, 1);
    engine.changed(); now += 1000; await engine.tick();
    assert.equal(writes, 1, "unchanged binary is not written again");
});

test("failed save retains pending changes and retries with backoff", async () => {
    let now = 0, calls = 0;
    const engine = new AutoSave(async () => content("current"), async () => { if (++calls === 1) throw new Error("mount disconnected"); }, () => {}, () => now);
    engine.start(); now = 1000; await engine.tick();
    assert.equal(engine.savedRevision, 0);
    now = 2000; await engine.tick(); assert.equal(calls, 1);
    now = 3000; await engine.tick(); assert.equal(calls, 2);
    assert.equal(engine.savedRevision, engine.revision);
});

test("disabled during export cannot write a stale snapshot", async () => {
    let finish, writes = 0;
    const engine = new AutoSave(() => new Promise(resolve => { finish = resolve; }), async () => writes++, () => {});
    engine.start(); const running = engine.tick(true);
    engine.stop(); finish(content("stale")); await running;
    assert.equal(writes, 0);
});

test("edits during export remain pending and save again", async () => {
    let now = 0, finish;
    const engine = new AutoSave(() => new Promise(resolve => { finish = resolve; }), async () => {}, () => {}, () => now);
    engine.start(); const running = engine.tick(true);
    now = 500; engine.changed(); finish(content("snapshot")); await running;
    assert.ok(engine.revision > engine.savedRevision);
    assert.equal(engine.dueAt, 1500);
});

test("busy export is never reentered and invalid export is never written", async () => {
    let finish, exports = 0, writes = 0;
    const engine = new AutoSave(() => { exports++; return new Promise(resolve => { finish = resolve; }); }, async () => writes++, () => {});
    engine.start(); const running = engine.tick(true);
    await engine.tick(true); assert.equal(exports, 1);
    finish(new ArrayBuffer(0)); await running;
    assert.equal(writes, 0); assert.equal(engine.savedRevision, 0);
});
