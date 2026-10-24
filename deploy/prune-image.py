"""Remove unused development headers and Docker-in-Docker packages, not runtime libraries."""
from pathlib import Path
import subprocess

installed = subprocess.check_output(
    ["dpkg-query", "-W", "-f=${binary:Package} ${db:Status-Status}\n"], text=True
)
docker_packages = {
    "docker-ce", "docker-ce-cli", "docker-buildx-plugin", "docker-compose-plugin",
    "docker-ce-rootless-extras", "containerd.io", "docker.io", "runc",
}
packages = []
for line in installed.splitlines():
    package, status = line.split()
    name = package.split(":")[0]
    if status == "installed" and (name.endswith("-dev") or name in docker_packages):
        packages.append(package)
if packages:
    subprocess.run(["apt-get", "purge", "-y", *packages], check=True)

init = Path("/etc/s6-overlay/s6-rc.d/init-selkies-config/run")
source = init.read_text()
start = source.index("# add proot-apps")
end = source.index("# Enable gpu encode", start)
init.write_text(source[:start] + "# Third-party application installer removed for WeChat-only deployment.\n" + source[end:])
Path("/etc/s6-overlay/s6-rc.d/user/contents.d/svc-docker").unlink(missing_ok=True)
