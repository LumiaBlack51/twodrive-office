using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace TwoDrive.Office
{
    [ComVisible(true), Guid("9C829C45-D993-46CF-82AB-D3BE4C1AC6BC"),
     InterfaceType(ComInterfaceType.InterfaceIsIDispatch)]
    public interface IAddInCallbacks
    {
        [DispId(10)] void Toggle(object control, bool pressed);
        [DispId(11)] bool Pressed(object control);
        [DispId(12)] void SaveNow(object control);
        [DispId(13)] string Status(object control);
        [DispId(14)] void RibbonLoaded(object ribbon);
    }

    [ComVisible(true), Guid("72D11CA7-C180-463F-BC48-822DC433A169"),
     ProgId("TwoDrive.Office.AutoSave"), ClassInterface(ClassInterfaceType.None),
     ComDefaultInterface(typeof(IAddInCallbacks))]
    public sealed class AddIn : IDTExtensibility2, IRibbonExtensibility, IAddInCallbacks
    {
        dynamic application, ribbon;
        string host;
        SaveController controller;
        Timer timer;
        bool running;
        string setupStatus = "请先配置 TwoDrive Development 同步目录";

        public void OnConnection(object app, int mode, object instance, ref Array custom)
        {
            application = app;
            try
            {
                string name = (string)application.Name;
                host = name.IndexOf("Word", StringComparison.OrdinalIgnoreCase) >= 0 ? "Word"
                    : name.IndexOf("Excel", StringComparison.OrdinalIgnoreCase) >= 0 ? "Excel"
                    : name.IndexOf("PowerPoint", StringComparison.OrdinalIgnoreCase) >= 0 ? "PowerPoint" : null;
                if (host == null) return;
                var settings = Settings.Load();
                Attach(app, host, new SaveController(new NativeRoot(settings), settings.IntervalSeconds));
            }
            catch (Exception) { setupStatus = "未启用：请检查 TwoDrive 同步根与插件设置"; }
        }
        // Kept internal (not a COM entry point) so the real host loop can be
        // tested with generated roots without changing the user's registration.
        internal void Attach(object app, string applicationHost, SaveController saveController)
        {
            if (timer != null) throw new InvalidOperationException("Already attached");
            application = app;
            host = applicationHost;
            controller = saveController;
            // WinForms timers run on Office's STA UI thread, never a thread pool.
            timer = new Timer { Interval = 1000 };
            timer.Tick += delegate { Poll(); };
            timer.Start();
        }
        void Poll()
        {
            if (running || controller == null || application == null) return;
            running = true;
            try
            {
                dynamic documents = host == "Word" ? application.Documents
                    : host == "Excel" ? application.Workbooks : application.Presentations;
                int count = documents.Count;
                var open = new HashSet<string>();
                bool complete = true;
                for (int i = 1; i <= count; i++)
                {
                    try
                    {
                        var document = new OfficeDocument((object)application, (object)documents.Item(i), host);
                        open.Add(document.Key);
                        controller.Tick(document, DateTime.UtcNow, false);
                    }
                    catch (Exception) { complete = false; }
                }
                if (complete) controller.ForgetExcept(open);
                if (ribbon != null) ribbon.Invalidate();
            }
            catch (Exception) { /* Never unwind through Office's message loop. */ }
            finally { running = false; }
        }
        OfficeDocument Active()
        {
            object doc = host == "Word" ? application.ActiveDocument
                : host == "Excel" ? application.ActiveWorkbook : application.ActivePresentation;
            return new OfficeDocument((object)application, doc, host);
        }
        public void Toggle(object control, bool pressed)
        {
            try { if (controller != null) controller.Pause(Active(), !pressed); }
            catch (Exception) { }
        }
        public bool Pressed(object control)
        {
            try { return controller != null && controller.IsEnabled(Active()); }
            catch (Exception) { return false; }
        }
        public void SaveNow(object control)
        {
            try { if (controller != null) controller.Tick(Active(), DateTime.UtcNow, true); }
            catch (Exception) { }
        }
        public string Status(object control) { return controller == null ? setupStatus : controller.Status; }
        public void RibbonLoaded(object value) { ribbon = value; }
        public string GetCustomUI(string ribbonId)
        {
            return @"<customUI xmlns='http://schemas.microsoft.com/office/2009/07/customui' onLoad='RibbonLoaded'>
              <ribbon><tabs><tab id='TwoDriveTab' label='TwoDrive'><group id='TwoDriveSave' label='自动保存'>
                <toggleButton id='TwoDriveEnabled' label='自动保存' getPressed='Pressed' onAction='Toggle'/>
                <button id='TwoDriveSaveNow' label='立即保存' onAction='SaveNow'/>
                <labelControl id='TwoDriveStatus' getLabel='Status'/>
              </group></tab></tabs></ribbon></customUI>";
        }
        void Stop()
        {
            if (timer != null) { timer.Stop(); timer.Dispose(); timer = null; }
            // Native Office close/save prompts remain responsible for edits that
            // have not yet reached the timer. Never force Saved=true on shutdown.
            controller = null;
            ribbon = null;
            application = null;
        }
        public void OnDisconnection(int mode, ref Array custom) { Stop(); }
        public void OnBeginShutdown(ref Array custom) { Stop(); }
        public void OnAddInsUpdate(ref Array custom) { }
        public void OnStartupComplete(ref Array custom) { }
    }
}
