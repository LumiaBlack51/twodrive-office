using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

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
        [DllImport("cldapi.dll")]
        static extern int CfGetSyncRootInfoByHandle(SafeFileHandle handle, int infoClass,
            byte[] buffer, uint length, out uint returned);
        [DllImport("cldapi.dll")]
        static extern uint CfGetPlaceholderStateFromAttributeTag(uint attributes, uint tag);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern SafeFileHandle CreateFile(string path, uint access, uint share,
            IntPtr security, uint creation, uint flags, IntPtr template);
        [StructLayout(LayoutKind.Sequential)]
        struct AttributeTag { public uint Attributes, Tag; }
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool GetFileInformationByHandleEx(SafeFileHandle handle, int infoClass,
            out AttributeTag info, uint size);

        public NativeRoot(Settings settings)
        {
            root = Path.GetFullPath(settings.RootPath).TrimEnd(Path.DirectorySeparatorChar);
            identity = settings.RootIdentity;
            if (String.IsNullOrEmpty(identity)) throw new IOException("缺少同步根身份");
        }
        public static string Identity(string path)
        {
            using (var handle=CreateFile(path,0x80,7,IntPtr.Zero,3,0x02200000,IntPtr.Zero))
            {
                AttributeTag attributes;
                if(handle.IsInvalid || !GetFileInformationByHandleEx(handle,9,out attributes,8))
                    throw new IOException("无法确认保存路径身份");
                if((attributes.Attributes & 0x400)!=0 &&
                   ((attributes.Tag & 0x20000000)!=0 || (CfGetPlaceholderStateFromAttributeTag(attributes.Attributes,attributes.Tag)&1)==0))
                    throw new IOException("保存路径包含不受支持的重解析点");
                return Identity(handle);
            }
        }
        static string Identity(SafeFileHandle handle)
        {
            // CF_SYNC_ROOT_STANDARD_INFO, native SDK layout (x86 and x64).
            byte[] data = new byte[1056 + 4096];
            uint returned;
            int result = CfGetSyncRootInfoByHandle(handle, 1, data, (uint)data.Length, out returned);
            if (result < 0 || returned < 1056) throw new IOException("未发现 TwoDrive 同步根");
            uint length = BitConverter.ToUInt32(data, 1052);
            if (length < 24 || length > 4096 || 1056 + length > returned)
                throw new IOException("同步根身份无效");
            string binding = Encoding.UTF8.GetString(data, 1056, (int)length);
            if (!binding.StartsWith("twodrive-dev-native-v1:", StringComparison.Ordinal))
                throw new IOException("此目录不是 TwoDrive Development 同步根");
            // Bind the actual root directory identity too, so replacing a root
            // with another registration bearing the same account label fails.
            return BitConverter.ToInt64(data,0).ToString("X16")+":"+Convert.ToBase64String(data, 1056, (int)length);
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
