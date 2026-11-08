using System;
using System.Collections.Generic;
using System.IO;
using TwoDrive.Office;

class ControllerTests
{
    sealed class Guard : IRootGuard
    {
        public bool Available = true;
        public int Calls;
        public void Validate(string path) { Calls++; if (!Available) throw new IOException(); }
    }
    sealed class Document : IDocument
    {
        public string Key { get { return "one"; } }
        public string FullName { get; set; }
        public bool Saved { get; set; }
        public bool Eligible { get; set; }
        public bool Fail, Cancel;
        public int Saves;
        public Action DuringSave;
        public void Save()
        {
            Saves++;
            if (Fail) throw new IOException();
            if (DuringSave != null) DuringSave();
            if (Cancel) return;
            File.WriteAllText(FullName, "saved new content");
            Saved = true;
        }
    }
    static void Check(bool value, string name) { if (!value) throw new Exception(name); }
    static int Main()
    {
        string root = Path.Combine(Path.GetTempPath(), "twodrive-office-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string path = Path.Combine(root, "sample.docx");
            File.WriteAllText(path, "original");
            var guard = new Guard();
            var document = new Document { FullName=path, Saved=true, Eligible=true };
            var scheduler = new SaveController(guard, 30);
            var now = new DateTime(2026,11,7,0,0,0,DateTimeKind.Utc);
            scheduler.Tick(document, now, false);
            Check(document.Saves == 0, "opening clean documents must not save");
            document.Saved = false;
            scheduler.Tick(document, now.AddSeconds(29), false);
            Check(document.Saves == 0, "interval");
            scheduler.Tick(document, now.AddSeconds(31), false);
            Check(document.Saves == 1 && document.Saved && guard.Calls >= 4, "native save and repeated validation");
            document.Saved = false;
            guard.Available = false;
            scheduler.Tick(document, now.AddSeconds(90), true);
            Check(document.Saves == 1 && !document.Saved, "disconnected root keeps dirty state");
            guard.Available = true;
            scheduler.Pause(document, true);
            scheduler.Tick(document, now.AddSeconds(120), true);
            Check(document.Saves == 1, "pause survives later ticks");
            scheduler.Pause(document, false);
            document.Fail = true;
            scheduler.Tick(document, now.AddSeconds(150), true);
            Check(document.Saves == 2 && !document.Saved, "failed save must stay dirty");
            document.Fail = false;
            document.Cancel = true;
            scheduler.Tick(document, now.AddSeconds(180), true);
            Check(document.Saves == 3 && !document.Saved, "cancelled native save is not acknowledged");
            document.Cancel = false;
            document.DuringSave = delegate { scheduler.Tick(document, now.AddSeconds(210), true); };
            scheduler.Tick(document, now.AddSeconds(210), true);
            Check(document.Saves == 4 && document.Saved, "save reentrancy");
            document.DuringSave = null;
            document.Saved = false;
            File.WriteAllText(path, "external writer changed the document");
            scheduler.Tick(document, now.AddSeconds(240), true);
            Check(document.Saves == 4 && !document.Saved, "external modifications block overwrite");
            document.FullName = Path.Combine(root, "elsewhere.docx");
            File.WriteAllText(document.FullName, "other");
            scheduler.Tick(document, now.AddSeconds(270), true);
            Check(document.Saves == 4 && scheduler.IsPaused(document), "Save As cannot silently retarget");
            scheduler.ForgetExcept(new HashSet<string>());
            document.Eligible = false;
            scheduler.Tick(document, now.AddSeconds(300), true);
            Check(document.Saves == 4, "unsupported format is never saved");
            Console.WriteLine("PASS: 11 MS Office save-controller scenarios (generated files; no Office or cloud)");
            return 0;
        }
        catch (Exception e) { Console.Error.WriteLine(e.Message); return 1; }
        finally { Directory.Delete(root, true); }
    }
}
