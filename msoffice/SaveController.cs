using System;
using System.Collections.Generic;
using System.IO;

namespace TwoDrive.Office
{
    public sealed class Stamp : IEquatable<Stamp>
    {
        public long Length, Write, Created;
        public static Stamp Read(string path)
        {
            var file = new FileInfo(path);
            if (!file.Exists) throw new IOException("原文件已移动或删除");
            return new Stamp { Length = file.Length, Write = file.LastWriteTimeUtc.Ticks,
                               Created = file.CreationTimeUtc.Ticks };
        }
        public bool Equals(Stamp other)
        {
            return other != null && Length == other.Length && Write == other.Write && Created == other.Created;
        }
    }

    public sealed class SaveController
    {
        sealed class Session
        {
            public string Path;
            public Stamp Expected;
            public DateTime Due;
            public bool Paused, Failed;
        }
        readonly Dictionary<string, Session> sessions = new Dictionary<string, Session>();
        readonly IRootGuard guard;
        readonly TimeSpan interval;
        bool saving;
        public string Status { get; private set; }
        public SaveController(IRootGuard guard, int seconds)
        {
            if (seconds < 5 || seconds > 3600) throw new ArgumentOutOfRangeException("seconds");
            this.guard = guard;
            interval = TimeSpan.FromSeconds(seconds);
            Status = "等待 TwoDrive 内已命名的文档";
        }

        public bool IsPaused(IDocument document)
        {
            Session state;
            return sessions.TryGetValue(document.Key, out state) && state.Paused;
        }
        public bool IsEnabled(IDocument document)
        {
            try
            {
                guard.Validate(document.FullName);
                return document.Eligible && !IsPaused(document);
            }
            catch (Exception) { return false; }
        }
        public void Pause(IDocument document, bool paused)
        {
            Session state;
            if (!sessions.TryGetValue(document.Key, out state))
            {
                state = new Session();
                sessions.Add(document.Key, state);
            }
            state.Paused = paused;
            Status = paused ? "此文档自动保存已暂停" : "此文档自动保存已开启";
        }
        public void ForgetExcept(HashSet<string> open)
        {
            foreach (string key in new List<string>(sessions.Keys))
                if (!open.Contains(key)) sessions.Remove(key);
        }
        public void Tick(IDocument document, DateTime now, bool force)
        {
            if (saving) return;
            saving = true;
            try
            {
                Session state;
                if (!sessions.TryGetValue(document.Key, out state))
                {
                    state = new Session();
                    sessions.Add(document.Key, state);
                }
                if (state.Paused) return;
                string path = document.FullName;
                guard.Validate(path);
                if (!document.Eligible) { Status = "此文档暂不支持自动保存，请手动保存"; return; }
                var stamp = Stamp.Read(path);
                // Never silently move an existing session to a Save As target.
                if (state.Path != null && !String.Equals(state.Path, path, StringComparison.OrdinalIgnoreCase))
                {
                    state.Paused = true;
                    Status = "另存位置已变化；请重新打开文档后启用";
                    return;
                }
                if (state.Path == null)
                {
                    state.Path = path;
                    state.Expected = stamp;
                    state.Due = now + interval;
                }
                if (document.Saved)
                {
                    state.Expected = stamp;
                    state.Due = now + interval;
                    state.Failed = false;
                    return;
                }
                if (!force && now < state.Due) return;
                if (!state.Expected.Equals(stamp))
                    throw new IOException("磁盘文件已被其他程序修改；请检查后手动保存");
                // Repeat immediately before the native Save; no export/format
                // conversion and never assign Office's Saved property ourselves.
                guard.Validate(path);
                if (!Stamp.Read(path).Equals(state.Expected))
                    throw new IOException("保存目标发生变化");
                document.Save();
                if (!document.Saved) throw new IOException("Office 尚未确认保存完成");
                state.Expected = Stamp.Read(path);
                state.Due = now + interval;
                state.Failed = false;
                Status = "已保存到本地；云端上传由 TwoDrive 完成";
            }
            catch (Exception)
            {
                // Keep the document dirty and back off. No raw private path,
                // password, COM exception or dialog is emitted from a timer.
                Session state;
                if (sessions.TryGetValue(document.Key, out state))
                {
                    state.Failed = true;
                    state.Due = now + interval;
                }
                Status = "自动保存未完成；修改仍在 Office 中，请检查连接或手动保存";
            }
            finally { saving = false; }
        }
    }
}
