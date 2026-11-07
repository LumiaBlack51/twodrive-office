using System;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;

namespace TwoDrive.Office
{
    public sealed class OfficeDocument : IDocument
    {
        readonly dynamic application;
        readonly dynamic document;
        readonly string host;
        readonly string key;
        public OfficeDocument(object app, object doc, string host)
        {
            application = app;
            document = doc;
            this.host = host;
            IntPtr identity = Marshal.GetIUnknownForObject(doc);
            try { key = identity.ToInt64().ToString(CultureInfo.InvariantCulture); }
            finally { Marshal.Release(identity); }
        }
        public string Key { get { return key; } }
        public string FullName { get { return (string)document.FullName; } }
        public bool Saved { get { return Convert.ToInt32(document.Saved) != 0; } }
        public bool Eligible
        {
            get
            {
                if (String.IsNullOrEmpty((string)document.Path) || Convert.ToInt32(document.ReadOnly) != 0)
                    return false;
                if (Convert.ToBoolean(document.HasVBProject) || Convert.ToBoolean(document.Permission.Enabled)
                    || Convert.ToInt32(document.Signatures.Count) != 0)
                    return false;
                string extension = Path.GetExtension(FullName).ToLowerInvariant();
                if (host == "Word")
                    return extension == ".docx" && !Convert.ToBoolean(document.HasPassword)
                        && Convert.ToInt32(document.ProtectionType) == -1
                        && Convert.ToInt32(application.BackgroundSavingStatus) == 0
                        && (Convert.ToInt32(document.SaveFormat) == 12 || Convert.ToInt32(document.SaveFormat) == 16);
                if (host == "Excel")
                    return extension == ".xlsx" && !Convert.ToBoolean(document.HasPassword)
                        && !Convert.ToBoolean(document.MultiUserEditing)
                        && Convert.ToBoolean(application.Ready)
                        && Convert.ToInt32(application.CalculationState) == 0
                        && Convert.ToInt32(document.FileFormat) == 51;
                // PowerPoint's Password getter returns a mask even without a
                // password, and Presentation has no FileFormat property.
                return extension == ".pptx" && PlainPresentation(FullName)
                    && Convert.ToInt32(application.SlideShowWindows.Count) == 0;
            }
        }
        static bool PlainPresentation(string path)
        {
            using (var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var zip = new ZipArchive(file, ZipArchiveMode.Read))
            {
                if (zip.Entries.Count > 100000 || zip.GetEntry("ppt/presentation.xml") == null) return false;
                foreach (var entry in zip.Entries)
                    if (entry.FullName.EndsWith("vbaProject.bin", StringComparison.OrdinalIgnoreCase)) return false;
                return true;
            }
        }
        public void Save()
        {
            object previous = application.DisplayAlerts;
            try
            {
                if (host == "Excel") application.DisplayAlerts = false;
                else application.DisplayAlerts = host == "Word" ? 0 : 1;
                document.Save();
            }
            finally { application.DisplayAlerts = previous; }
        }
    }
}
