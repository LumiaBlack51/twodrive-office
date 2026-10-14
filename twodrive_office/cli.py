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
    sub.add_parser("serve", help="启动 ONLYOFFICE 的本地桥接服务")
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
            server = make_server(Bridge(settings))
            print(f"TwoDrive Office 本地服务：http://127.0.0.1:{settings.bridge_port}", flush=True)
            try:
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
