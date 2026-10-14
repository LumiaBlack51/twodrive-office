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
    sub.add_parser("status", help="检查设置和挂载（不显示凭据）")
    serve = sub.add_parser("serve", help="启动 ONLYOFFICE 的本地桥接服务")
    serve.add_argument("--idle-timeout", type=float, default=0,
                       help="ONLYOFFICE 退出且没有请求后自动退出的秒数；0 为手动常驻")
    args = parser.parse_args()
    try:
        settings = Settings.load(args.config)
        if args.command == "configure":
            if args.mount_root:
                root = normalized(args.mount_root)
                settings = replace(settings, mount_root=root, copy_directory=root / "Office AutoSave")
            if args.copy_directory:
                settings = replace(settings, copy_directory=normalized(args.copy_directory))
            settings = settings.save(args.config)
            print("设置已保存：", args.config)
        elif args.command == "serve":
            from .bridge import Bridge, make_server
            from .lifecycle import OfficePresence, activation_socket
            if args.idle_timeout < 0:
                raise SaveError("退出延迟不能为负数")
            server = make_server(Bridge(settings), listener=activation_socket(settings.bridge_port))
            print(f"TwoDrive Office 本地服务：http://127.0.0.1:{settings.bridge_port}", flush=True)
            try:
                if args.idle_timeout:
                    office = OfficePresence()
                    server.timeout = 1
                    while True:
                        server.handle_request()
                        if server.activity.idle(args.idle_timeout) and not office.running():
                            print("ONLYOFFICE 已关闭，桥接服务按需退出", flush=True)
                            break
                else:
                    server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return
        settings.check_mount()
        print("TwoDrive 挂载可用：", settings.mount_root)
        print("默认副本目录：", settings.copy_directory)
    except (SaveError, ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
