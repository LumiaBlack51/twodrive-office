using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;

namespace TwoDrive.Office
{
    public sealed class Settings
    {
        public string RootPath { get; set; }
        public string RootIdentity { get; set; }
        public int IntervalSeconds { get; set; }
        public static string FileName
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                      "TwoDrive-Office", "msoffice.json"); }
        }
        public static Settings Load()
        {
            var value = new JavaScriptSerializer().Deserialize<Settings>(File.ReadAllText(FileName));
            if (value == null || value.IntervalSeconds < 5 || value.IntervalSeconds > 3600)
                throw new IOException("自动保存设置无效");
            return value;
        }
    }

    public sealed class NativeRoot : IRootGuard
    {
        readonly string root;
        readonly string identity;
        [DllImport("cldapi.dll", CharSet = CharSet.Unicode)]
        static extern int CfGetSyncRootInfoByPath(string path, int infoClass,
            byte[] buffer, uint length, out uint returned);

        public NativeRoot(Settings settings)
        {
            root = Path.GetFullPath(settings.RootPath).TrimEnd(Path.DirectorySeparatorChar);
            identity = settings.RootIdentity;
            if (String.IsNullOrEmpty(identity)) throw new IOException("缺少同步根身份");
        }
        public static string Identity(string path)
        {
            // CF_SYNC_ROOT_STANDARD_INFO, native SDK layout (x86 and x64).
            byte[] data = new byte[1056 + 4096];
            uint returned;
            int result = CfGetSyncRootInfoByPath(path, 1, data, (uint)data.Length, out returned);
            if (result < 0 || returned < 1056) throw new IOException("未发现 TwoDrive 同步根");
            uint length = BitConverter.ToUInt32(data, 1052);
            if (length < 24 || length > 4096 || 1056 + length > returned)
                throw new IOException("同步根身份无效");
            string binding = Encoding.UTF8.GetString(data, 1056, (int)length);
            if (!binding.StartsWith("twodrive-dev-native-v1:", StringComparison.Ordinal))
                throw new IOException("此目录不是 TwoDrive Development 同步根");
            return Convert.ToBase64String(data, 1056, (int)length);
        }
        public void Validate(string fullName)
        {
            if (String.IsNullOrEmpty(fullName) || !Path.IsPathRooted(fullName))
                throw new IOException("请先命名并保存文档");
            string path = Path.GetFullPath(fullName);
            if (!path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new IOException("文档不在配置的 TwoDrive 目录内");
            // Every ancestor must belong to the same live CFAPI registration.
            // A junction escaping the root changes the queried root identity.
            for (string current = path; ; current = Path.GetDirectoryName(current))
            {
                if (String.IsNullOrEmpty(current) || Identity(current) != identity)
                    throw new IOException("同步根已断开、替换或切换账号");
                if (String.Equals(current, root, StringComparison.OrdinalIgnoreCase)) break;
            }
            var attrs = File.GetAttributes(path);
            if ((attrs & (FileAttributes.ReadOnly | FileAttributes.Offline)) != 0)
                throw new IOException("文档只读或尚未完成下载");
        }
    }
}
