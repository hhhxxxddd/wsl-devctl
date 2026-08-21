from __future__ import annotations

import getpass
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from wsl_devctl.config import parse_project
from wsl_devctl.discovery import build_project_config, default_name, discover
from wsl_devctl.tomlgen import render_toml


class DiscoveryTests(unittest.TestCase):
    def test_next_project_generates_safe_host_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "News Reader"
            source.mkdir()
            (source / "package.json").write_text(
                json.dumps(
                    {
                        "packageManager": "pnpm@10.0.0",
                        "dependencies": {"next": "16.0.0", "react": "19.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            detection = discover(str(source))
            self.assertEqual(detection.framework, "next")
            self.assertEqual(detection.package_manager, "pnpm")
            self.assertEqual(default_name(source), "local-news-reader")
            raw = build_project_config(detection, default_name(source), getpass.getuser())
            self.assertEqual(raw["frontend"]["port"], 3000)
            self.assertIn("--hostname 0.0.0.0", raw["frontend"]["run"])
            self.assertIn("**/.next/", raw["sync"]["exclude"])
            rendered = render_toml(raw)
            parsed = tomllib.loads(rendered)
            project = parse_project(parsed)
            self.assertEqual(project.runtime_driver, "host")

    def test_vite_project_uses_vite_host_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "web"
            source.mkdir()
            (source / "package.json").write_text(
                json.dumps({"devDependencies": {"vite": "7.0.0"}}),
                encoding="utf-8",
            )
            (source / "package-lock.json").write_text("{}", encoding="utf-8")
            detection = discover(str(source))
            raw = build_project_config(detection, "dev-web", getpass.getuser())
            self.assertEqual(raw["frontend"]["port"], 5173)
            self.assertIn("--host 0.0.0.0", raw["frontend"]["run"])

    def test_vite_reads_configured_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "web"
            source.mkdir()
            (source / "package.json").write_text(
                json.dumps({"devDependencies": {"vite": "7.0.0"}}),
                encoding="utf-8",
            )
            (source / "package-lock.json").write_text("{}", encoding="utf-8")
            (source / "vite.config.ts").write_text(
                "export default { server: { port: Number(getEnv('VITE_PORT', '5175')) } }\n",
                encoding="utf-8",
            )
            detection = discover(str(source))
            raw = build_project_config(detection, "dev-web", getpass.getuser())
            self.assertEqual(raw["frontend"]["port"], 5175)

    def test_multimodule_spring_selects_application_module_and_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "workspace"
            backend = source / "backend"
            app = backend / "app-server"
            library = backend / "app-domain"
            frontend = source / "frontend"
            for path in (app / "src/main/resources", library / "src/main/java", frontend):
                path.mkdir(parents=True)
            (app / "src/main/java").mkdir(parents=True)
            (backend / "pom.xml").write_text(
                """<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>dev.test</groupId><artifactId>backend</artifactId><version>1</version>
  <packaging>pom</packaging>
  <properties><spring.boot.version>4.1.0</spring.boot.version></properties>
  <modules><module>app-domain</module><module>app-server</module></modules>
</project>
""",
                encoding="utf-8",
            )
            (library / "pom.xml").write_text(
                """<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>dev.test</groupId><artifactId>app-domain</artifactId><version>1</version>
</project>
""",
                encoding="utf-8",
            )
            (app / "pom.xml").write_text(
                """<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>dev.test</groupId><artifactId>app-server</artifactId><version>1</version>
  <dependencies><dependency>
    <groupId>dev.test</groupId><artifactId>app-domain</artifactId><version>1</version>
  </dependency></dependencies>
  <build><plugins><plugin>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-maven-plugin</artifactId>
  </plugin></plugins></build>
</project>
""",
                encoding="utf-8",
            )
            (app / "src/main/resources/application-local.yaml").write_text(
                "server:\n  port: 48090\n",
                encoding="utf-8",
            )
            (frontend / "package.json").write_text(
                json.dumps(
                    {
                        "packageManager": "pnpm@10.0.0",
                        "devDependencies": {"vite": "7.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            (frontend / "vite.config.ts").write_text(
                "export default { server: { port: 5175 } }\n",
                encoding="utf-8",
            )

            detection = discover(str(source), runtime="host")
            raw = build_project_config(detection, "dev-workspace", getpass.getuser())

            self.assertEqual(raw["backend"]["workdir"], "backend")
            self.assertEqual(raw["backend"]["port"], 48090)
            self.assertIn("-pl app-server spring-boot:run", raw["backend"]["run"])
            self.assertIn("profiles=local", raw["backend"]["run"])
            self.assertEqual(raw["frontend"]["port"], 5175)
            self.assertIn("backend/app-server/src/main", raw["compile"]["watch"])
            self.assertIn("backend/app-domain/src/main", raw["compile"]["watch"])
            self.assertEqual(
                raw["java"]["spring"]["devtools"],
                "org.springframework.boot:spring-boot-devtools:4.1.0",
            )
            self.assertIn(
                "backend/app-domain/target/classes",
                raw["java"]["spring"]["classpath_modules"],
            )

    def test_compose_file_selects_compose_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "stack"
            source.mkdir()
            (source / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            detection = discover(str(source))
            raw = build_project_config(detection, "dev-stack", getpass.getuser())
            self.assertEqual(raw["runtime"]["driver"], "compose")
            self.assertEqual(raw["docker"]["compose"]["files"], ["compose.yaml"])
            self.assertFalse(raw["backend"]["enabled"])

    def test_monorepo_selects_nested_next_and_root_package_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "workspace"
            app = source / "apps/web"
            app.mkdir(parents=True)
            (source / "package.json").write_text(
                json.dumps({"packageManager": "pnpm@10.0.0", "workspaces": ["apps/*"]}),
                encoding="utf-8",
            )
            (app / "package.json").write_text(
                json.dumps({"dependencies": {"next": "16.0.0"}}),
                encoding="utf-8",
            )
            detection = discover(str(source), runtime="host")
            self.assertEqual(detection.package_file, app / "package.json")
            self.assertEqual(detection.package_manager, "pnpm")
            raw = build_project_config(detection, "dev-workspace", getpass.getuser())
            self.assertEqual(raw["frontend"]["workdir"], "apps/web")


if __name__ == "__main__":
    unittest.main()
