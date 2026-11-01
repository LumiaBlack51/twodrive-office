import argparse
from dataclasses import replace
from pathlib import Path

from .policy import Settings, SaveError, config_path, normalized


def main():
    parser = argparse.ArgumentParser(description="TwoDrive Office AutoSave 本地设置和服务")
    parser.add_argument("--config", type=Path, default=config_path())
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("configure", help="保存挂载目录和默认副本目录")
    configure.add_argument("--mount-root")
    configure.add_argument("--copy-directory")
    configure.add_argument("--sync-interval", type=float, help="发布到 TwoDrive 的间隔秒数（默认 180）")
    sub.add_parser("status", help="检查设置和挂载（不显示凭据）")
    sub.add_parser("pending", help="列出保留的本地待同步版本（不修改或提交）")
    sub.add_parser("recover", help="所有办公软件退出后，安全重试保留的本地版本")
    serve = sub.add_parser("serve", help="启动 ONLYOFFICE 的本地桥接服务")
    serve.add_argument("--idle-timeout", type=float, default=0,
                       help="ONLYOFFICE 退出且没有请求后自动退出的秒数；0 为手动常驻")
    args = parser.parse_args()
    try:
        settings = Settings.load(args.config)
        if args.command == "pending":
            import json
            from .local_save import state_directory
            for manifest in state_directory().glob("*/pending.json"):
                data = json.loads(manifest.read_text())
                print(json.dumps({"target": data["target"], "local_path": str(manifest.parent / data["blob"])}, ensure_ascii=False))
            return
        if args.command == "recover":
            from .lifecycle import OfficePresence
            from .local_save import LocalSaveQueue
            if OfficePresence(names=("DesktopEditors", "desktopeditors", "soffice.bin")).running():
                raise SaveError("请先正常关闭办公软件，再恢复本地待同步版本")
            queue = LocalSaveQueue(settings)
            try:
                queue.allow_recovery()
                queue.tick()
                if queue.pending():
                    raise SaveError("仍有未提交版本；请用 pending 查看，检查挂载或目标冲突")
            finally:
                queue.shutdown()
            print("本地待同步版本已提交 TwoDrive；云端完成状态由 TwoDrive 管理")
            return
        if args.command == "configure":
            if args.mount_root:
                root = normalized(args.mount_root)
                settings = replace(settings, mount_root=root, copy_directory=root / "Office AutoSave")
            if args.copy_directory:
                settings = replace(settings, copy_directory=normalized(args.copy_directory))
            if args.sync_interval is not None:
                settings = replace(settings, sync_interval_seconds=args.sync_interval)
            settings = settings.save(args.config)
            print("设置已保存：", args.config)
        elif args.command == "serve":
            from .bridge import Bridge, make_server
            from .lifecycle import OfficePresence, activation_socket
            if args.idle_timeout < 0:
                raise SaveError("退出延迟不能为负数")
            bridge = Bridge(settings)
            server = make_server(bridge, listener=activation_socket(settings.bridge_port))
            print(f"TwoDrive Office 本地服务：http://127.0.0.1:{settings.bridge_port}", flush=True)
            try:
                if args.idle_timeout:
                    office = OfficePresence()
                    all_office = OfficePresence(names=("DesktopEditors", "desktopeditors", "soffice.bin"))
                    server.timeout = 1
                    while True:
                        server.handle_request()
                        bridge.local_saves.tick()
                        if server.activity.idle(args.idle_timeout) and not office.running():
                            for key in list(bridge.sessions):
                                bridge.close(key)
                            if not all_office.running():
                                bridge.local_saves.allow_recovery()
                                bridge.local_saves.tick()
                            if bridge.local_saves.pending():
                                continue
                            print("ONLYOFFICE 已关闭，桥接服务按需退出", flush=True)
                            break
                else:
                    server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
                bridge.local_saves.shutdown()
            return
        settings.check_mount()
        print("TwoDrive 挂载可用：", settings.mount_root)
        print("默认副本目录：", settings.copy_directory)
    except (SaveError, ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
