// Explicit native-host check: hidden owned COM instances, generated documents,
// no registration, no real mount, no user documents. Not run on hosted CI.
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;
using TwoDrive.Office;

class HostSmoke
{
    [DllImport("cldapi.dll",CharSet=CharSet.Unicode)]
    static extern int CfOpenFileWithOplock(string path,uint flags,out IntPtr handle);
    [DllImport("cldapi.dll")]
    static extern void CfCloseHandle(IntPtr handle);
    sealed class GeneratedRoot : IRootGuard
    {
        readonly string root;
        public GeneratedRoot(string root) { this.root = root; }
        public void Validate(string path)
        {
            if (!Path.GetFullPath(path).StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new IOException("outside generated root");
        }
    }
    static void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
    static void PumpUntil(Func<bool> predicate, int seconds)
    {
        var deadline=DateTime.UtcNow.AddSeconds(seconds);
        while (DateTime.UtcNow < deadline && !predicate())
        {
            System.Windows.Forms.Application.DoEvents();
            System.Threading.Thread.Sleep(50);
        }
        Check(predicate(), "Office timer did not finish the native save");
    }
    static void Content(string path, string part, string expected)
    {
        using (var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        using (var zip = new ZipArchive(file, ZipArchiveMode.Read))
        using (var stream = new StreamReader(zip.GetEntry(part).Open()))
            Check(stream.ReadToEnd().Contains(expected), "persisted Office content did not match");
    }
    [STAThread]
    static int Main(string[] args)
    {
        foreach (string name in new[] { "WINWORD", "EXCEL", "POWERPNT" })
            if (Process.GetProcessesByName(name).Length != 0)
            { Console.Error.WriteLine("Office is already running; native test refused."); return 2; }
        bool native=args.Length==2 && args[0]=="--native-root";
        if(args.Length!=0 && !native) return 2;
        string root = native ? Path.GetFullPath(args[1]) : Path.Combine(Path.GetTempPath(), "twodrive-msoffice-host-" + Guid.NewGuid().ToString("N"));
        if(native)
        {
            Check(Path.GetFileName(root).StartsWith("twodrive-cfapi-fixture-office-",StringComparison.Ordinal)
                && Directory.Exists(root) && Directory.GetFileSystemEntries(root).Length==0,"not an empty generated CFAPI fixture");
        }
        else Directory.CreateDirectory(root);
        IRootGuard guard=native ? (IRootGuard)new NativeRoot(new Settings {RootPath=root,RootIdentity=NativeRoot.Identity(root),IntervalSeconds=5}) : new GeneratedRoot(root);
        try
        {
            foreach (string host in new[] { "Word", "Excel", "PowerPoint" })
            {
                dynamic app = null, doc = null;
                AddIn addIn = null;
                try
                {
                    Console.WriteLine(host + ": create hidden instance");
                    app = Activator.CreateInstance(Type.GetTypeFromProgID(host + ".Application", true));
                    if (host != "PowerPoint") app.Visible = false;
                    string path = Path.Combine(root, "generated." + (host == "Word" ? "docx" : host == "Excel" ? "xlsx" : "pptx"));
                    if (host == "Word")
                    {
                        Check(app.Documents.Count == 0, "unexpected Word documents");
                        doc = app.Documents.Add(Visible: false);
                        doc.Content.Text = "original";
                        doc.SaveAs2(path, 12, AddToRecentFiles: false);
                    }
                    else if (host == "Excel")
                    {
                        Check(app.Workbooks.Count == 0, "unexpected Excel workbooks");
                        doc = app.Workbooks.Add();
                        doc.Worksheets.Item(1).Cells.Item(1, 1).Value2 = "original";
                        doc.SaveAs(path, 51, AddToMru: false);
                    }
                    else
                    {
                        Check(app.Presentations.Count == 0, "unexpected PowerPoint presentations");
                        doc = app.Presentations.Add(0);
                        doc.Slides.Add(1, 12);
                        doc.SaveAs(path, 24);
                    }
                    Console.WriteLine(host + ": generated initial document saved");
                    var document = new OfficeDocument((object)app, (object)doc, host);
                    var controller = new SaveController(guard, 30);
                    Console.WriteLine(host + ": checking eligibility");
                    Check(document.Eligible, host + " eligibility rejected generated document");
                    DateTime now = DateTime.UtcNow;
                    controller.Tick(document, now, false);
                    if (host == "Word") doc.Content.Text = "TwoDriveNativeSaved";
                    else if (host == "Excel") doc.Worksheets.Item(1).Cells.Item(1, 1).Value2 = "TwoDriveNativeSaved";
                    else doc.Slides.Item(1).Shapes.AddTextbox(1, 10, 10, 300, 100).TextFrame.TextRange.Text = "TwoDriveNativeSaved";
                    Check(!document.Saved, host + " edit was not dirty");
                    object previousAlerts = app.DisplayAlerts;
                    controller.Tick(document, now.AddSeconds(31), false);
                    Check(document.Saved, host + " save not acknowledged: " + controller.Status);
                    Check(Object.Equals(previousAlerts, (object)app.DisplayAlerts), host + " alerts were not restored");
                    string part = host == "Word" ? "word/document.xml" : host == "Excel" ? "xl/sharedStrings.xml" : "ppt/slides/slide1.xml";
                    Content(path, part, "TwoDriveNativeSaved");
                    Console.WriteLine("PASS " + host + " native Save, dirty-state acknowledgement, persisted OOXML and alert restoration");
                    addIn = new AddIn();
                    addIn.Attach((object)app,host,new SaveController(guard,5));
                    // Establish a clean session through the actual timer, then
                    // edit and wait for its automatic enumeration/save path.
                    var begin=DateTime.UtcNow;
                    PumpUntil(delegate { return DateTime.UtcNow-begin > TimeSpan.FromSeconds(2); },5);
                    if(host=="Word") doc.Content.Text="TwoDriveTimerSaved";
                    else if(host=="Excel") doc.Worksheets.Item(1).Cells.Item(1,1).Value2="TwoDriveTimerSaved";
                    else doc.Slides.Item(1).Shapes.Item(1).TextFrame.TextRange.Text="TwoDriveTimerSaved";
                    Check(!document.Saved,"timer edit not dirty");
                    PumpUntil(delegate { return document.Saved; },20);
                    Content(path,part,"TwoDriveTimerSaved");
                    Console.WriteLine("PASS " + host + " actual STA timer enumeration and automatic Save");
                    if(native)
                    {
                        // Measure the sync adapter's actual exclusive-capture
                        // boundary while the editor remains open after Save.
                        try
                        {
                            using(var lease=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.None))
                                Console.WriteLine("CAPTURE_LEASE " + host + " available while open");
                        }
                        catch(IOException) { Console.WriteLine("CAPTURE_LEASE " + host + " busy while open"); }
                        IntPtr protectedHandle;
                        int result=CfOpenFileWithOplock(path,0,out protectedHandle);
                        if(result>=0)
                        {
                            try { Console.WriteLine("READ_OPLOCK " + host + " available while open"); }
                            finally { CfCloseHandle(protectedHandle); }
                        }
                        else Console.WriteLine("READ_OPLOCK " + host + " unavailable " + result.ToString("X8"));
                    }
                }
                finally
                {
                    if(addIn!=null) { Array custom=new object[0]; addIn.OnDisconnection(0,ref custom); }
                    if (doc != null)
                    {
                        try { if (host == "Word") doc.Close(0); else doc.Close(); }
                        catch (Exception) { }
                        Marshal.FinalReleaseComObject((object)doc);
                    }
                    if (app != null)
                    {
                        try { if (host == "Word") app.Quit(0); else app.Quit(); }
                        catch (Exception) { }
                        Marshal.FinalReleaseComObject((object)app);
                    }
                    GC.Collect(); GC.WaitForPendingFinalizers();
                }
            }
            return 0;
        }
        catch (Exception e) { Console.Error.WriteLine(e.ToString()); return 1; }
        finally
        {
            if(!native) Directory.Delete(root,true);
            else foreach(string name in new[]{"generated.docx","generated.xlsx","generated.pptx"})
                File.Delete(Path.Combine(root,name));
        }
    }
}
