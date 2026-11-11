#!/usr/bin/env python3
"""
Smite Panel CLI
"""
import os
import sys
import subprocess
import argparse
import getpass
import tempfile
import shutil
from pathlib import Path

try:
    import requests
except ImportError:
    try:
        import urllib.request
        import urllib.parse
        import json as json_lib
        HAS_REQUESTS = False
    except ImportError:
        print("Error: Please install requests: pip install requests")
        sys.exit(1)
else:
    HAS_REQUESTS = True


def get_compose_file():
    """Get docker-compose file path"""
    possible_roots = [
        Path("/opt/smite"),
        Path.cwd(),
        Path(__file__).parent.parent,
    ]
    
    for project_root in possible_roots:
        root_compose = project_root / "docker-compose.yml"
        if root_compose.exists():
            return root_compose
        docker_compose = project_root / "docker" / "docker-compose.panel.yml"
        if docker_compose.exists():
            return docker_compose
    
    return Path("/opt/smite") / "docker-compose.yml"


def get_env_file():
    """Get .env file path"""
    possible_roots = [
        Path("/opt/smite"),
        Path.cwd(),
        Path(__file__).parent.parent,
    ]
    
    for project_root in possible_roots:
        env_file = project_root / ".env"
        if env_file.exists():
            return env_file
    
    return Path("/opt/smite") / ".env"


def get_panel_port():
    """Get panel port from .env file"""
    env_file = get_env_file()
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("PANEL_PORT="):
                try:
                    return int(line.split("=")[1].strip())
                except ValueError:
                    return 8000
    return 8000


def get_panel_url():
    """Get panel API URL"""
    port = get_panel_port()
    return f"http://localhost:{port}"


def run_docker_compose(args, capture_output=False, env_vars=None, profile=None):
    """Run docker compose command"""
    compose_file = get_compose_file()
    if not compose_file.exists():
        print(f"Error: docker-compose.yml not found at {compose_file}")
        sys.exit(1)
    
    compose_dir = compose_file.parent
    env_file = compose_dir / ".env"
    if env_vars is None:
        env_vars = os.environ.copy()
    else:
        env_vars = env_vars.copy()
    
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                parts = line.split('=', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    if key:
                        env_vars[key] = value
    
    original_cwd = Path.cwd()
    
    try:
        os.chdir(compose_dir)
        if profile:
            env_vars["COMPOSE_PROFILES"] = profile
        cmd = ["docker", "compose", "-f", str(compose_file)] + args
        result = subprocess.run(cmd, capture_output=capture_output, text=True, cwd=str(compose_dir), env=env_vars)
        if not capture_output and result.returncode != 0:
            sys.exit(result.returncode)
        return result
    finally:
        os.chdir(original_cwd)


def cmd_admin_create(args):
    """Create admin user"""
    username = args.username or input("Username: ")
    
    if args.password:
        password = args.password
    else:
        while True:
            password = getpass.getpass("Password: ")
            password_confirm = getpass.getpass("Confirm Password: ")
            if password == password_confirm:
                break
            else:
                print("Passwords do not match. Please try again.")
    
    try:
        check_result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=smite-panel", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if check_result.returncode != 0 or not check_result.stdout.strip():
            print("Container 'smite-panel' not found.")
            print("\nStarting the panel...")
            compose_file = get_compose_file()
            if not compose_file.exists():
                print(f"Error: docker-compose.yml not found at {compose_file}")
                sys.exit(1)
            print(f"Using compose file: {compose_file}")
            start_result = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                capture_output=False,
                text=True,
                timeout=120
            )
            if start_result.returncode != 0:
                print(f"\nFailed to start panel (exit code: {start_result.returncode})")
                print("Please check: docker compose -f docker-compose.yml up -d")
                sys.exit(1)
            print("\nPanel started. Waiting for it to be ready...")
            import time
            time.sleep(5)
            check_result = subprocess.run(
                ["docker", "ps", "-a", "--filter", "name=smite-panel", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if not check_result.stdout.strip():
                print("Error: Container still not found after starting.")
                sys.exit(1)
        
        container_name = check_result.stdout.strip()
        
        max_wait = 30
        waited = 0
        import time
        
        print(f"Waiting for container {container_name} to be ready...", end="", flush=True)
        
        while waited < max_wait:
            result = subprocess.run(
                ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Status}}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                status = result.stdout.strip()
                if "Up" in status and "Restarting" not in status:
                    print(" ✓")
                    break
                elif "Restarting" in status:
                    print(".", end="", flush=True)
                elif "Exited" in status or "Dead" in status:
                    print(f"\nContainer is stopped (status: {status})")
                    print("Attempting to start container...")
                    compose_file = get_compose_file()
                    start_result = subprocess.run(
                        ["docker", "compose", "-f", str(compose_file), "start", "smite-panel"],
                        capture_output=True,
                        text=True
                    )
                    if start_result.returncode == 0:
                        print("Container started. Waiting...")
                        time.sleep(5)
                        continue
                    else:
                        print(f"Failed to start container: {start_result.stderr}")
                        sys.exit(1)
                else:
                    print(f"\nContainer status: {status}")
                    print("Waiting for container to be ready...")
            print(".", end="", flush=True)
            time.sleep(2)
            waited += 2
        else:
            print("\nTimeout waiting for container to be ready.")
            print("Please check container status: docker ps -a | grep smite-panel")
            sys.exit(1)
        
        if container_name:
            print(f"Creating admin via Docker container ({container_name})...")
            
            username_repr = repr(username)
            password_repr = repr(password)
            
            script_content = f"""import asyncio
import sys
import os
sys.path.insert(0, '/app')
from app.database import AsyncSessionLocal, init_db
from app.models import Admin
from sqlalchemy import select
from passlib.context import CryptContext

username = {username_repr}
password = {password_repr}

if isinstance(password, str):
    password_bytes = password.encode('utf-8')
    if len(password_bytes) > 72:
        password = password_bytes[:72].decode('utf-8', errors='ignore')

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def create():
    await init_db()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Admin).where(Admin.username == username))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Error: Admin user '{{username}}' already exists", file=sys.stderr)
            sys.exit(1)
        
        try:
            password_hash = pwd_context.hash(password)
        except Exception as e:
            print(f"Error hashing password: {{e}}", file=sys.stderr)
            sys.exit(1)
        admin = Admin(username=username, password_hash=password_hash)
        session.add(admin)
        await session.commit()
        print(f"Admin user '{{username}}' created successfully!")

asyncio.run(create())
"""
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as tmp_file:
                tmp_file.write(script_content)
                tmp_file_path = tmp_file.name
            
            try:
                copy_proc = subprocess.run(
                    ["docker", "cp", tmp_file_path, f"{container_name}:/tmp/create_admin.py"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if copy_proc.returncode != 0:
                    import base64
                    script_b64 = base64.b64encode(script_content.encode()).decode()
                    script_one_liner = f"PYTHONPATH=/app echo {script_b64} | base64 -d | python3"
                    proc = subprocess.run(
                        ["docker", "exec", "-e", "PYTHONPATH=/app", container_name, "sh", "-c", script_one_liner],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                else:
                    proc = subprocess.run(
                        ["docker", "exec", "-e", "PYTHONPATH=/app", container_name, "python", "/tmp/create_admin.py"],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
            finally:
                try:
                    os.unlink(tmp_file_path)
                except:
                    pass
            
            if proc.returncode == 0:
                print(proc.stdout)
                return
            else:
                error_msg = proc.stderr.strip() or proc.stdout.strip()
                if "already exists" in error_msg:
                    print(error_msg)
                    sys.exit(1)
                print(f"Warning: Docker exec failed: {error_msg}")
                print("Checking container logs...")
                log_proc = subprocess.run(
                    ["docker", "logs", "--tail", "10", container_name],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if log_proc.returncode == 0:
                    print("\nContainer logs:")
                    print(log_proc.stdout)
                print("\nTrying local method...")
        else:
            print("Warning: Container not running or still restarting. Checking container status...")
            status_proc = subprocess.run(
                ["docker", "ps", "-a", "--filter", "name=smite-panel", "--format", "table {{.Names}}\\t{{.Status}}"],
                capture_output=True,
                text=True
            )
            if status_proc.returncode == 0:
                print(status_proc.stdout)
            print("\nTrying local method...")
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError) as e:
        print(f"Warning: Docker error: {e}")
        print("Trying local method...")
    
    try:
        possible_roots = [
            Path(__file__).parent.parent,
            Path("/opt/smite"),
            Path.cwd(),
        ]
        
        panel_path = None
        for root in possible_roots:
            test_path = root / "panel"
            if test_path.exists() and (test_path / "main.py").exists():
                panel_path = test_path
                break
        
        if not panel_path:
            print("Error: Panel directory not found")
            print(f"Searched in: {[str(p / 'panel') for p in possible_roots]}")
            sys.exit(1)
        
        sys.path.insert(0, str(panel_path))
        
        from app.database import AsyncSessionLocal, init_db
        from app.models import Admin
        from sqlalchemy import select
        from passlib.context import CryptContext
        import asyncio
        
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        
        async def create():
            await init_db()
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Admin).where(Admin.username == username))
                existing = result.scalar_one_or_none()
                if existing:
                    print(f"Error: Admin user '{username}' already exists")
                    return
                
                password_hash = pwd_context.hash(password)
                admin = Admin(username=username, password_hash=password_hash)
                session.add(admin)
                await session.commit()
                print(f"Admin user '{username}' created successfully!")
        
        asyncio.run(create())
        
    except ImportError:
        print("Error: Panel dependencies not installed and Docker method failed.")
        print("\nPlease either:")
        print("  1. Start the panel: docker compose up -d")
        print("  2. Install dependencies: pip install -r panel/requirements.txt")
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to create admin: {e}")
        sys.exit(1)


def cmd_admin_update(args):
    """Update admin password"""
    if args.password:
        password = args.password
    else:
        while True:
            password = getpass.getpass("Password: ")
            password_confirm = getpass.getpass("Confirm Password: ")
            if password == password_confirm:
                break
            else:
                print("Passwords do not match. Please try again.")
    
    try:
        check_result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=smite-panel", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if check_result.returncode != 0 or not check_result.stdout.strip():
            print("Container 'smite-panel' not found.")
            print("\nStarting the panel...")
            compose_file = get_compose_file()
            if not compose_file.exists():
                print(f"Error: docker-compose.yml not found at {compose_file}")
                sys.exit(1)
            print(f"Using compose file: {compose_file}")
            start_result = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                capture_output=False,
                text=True,
                timeout=120
            )
            if start_result.returncode != 0:
                print(f"\nFailed to start panel (exit code: {start_result.returncode})")
                print("Please check: docker compose -f docker-compose.yml up -d")
                sys.exit(1)
            print("\nPanel started. Waiting for it to be ready...")
            import time
            time.sleep(5)
            check_result = subprocess.run(
                ["docker", "ps", "-a", "--filter", "name=smite-panel", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if not check_result.stdout.strip():
                print("Error: Container still not found after starting.")
                sys.exit(1)
        
        container_name = check_result.stdout.strip()
        
        max_wait = 30
        waited = 0
        import time
        
        print(f"Waiting for container {container_name} to be ready...", end="", flush=True)
        
        while waited < max_wait:
            result = subprocess.run(
                ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Status}}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                status = result.stdout.strip()
                if "Up" in status and "Restarting" not in status:
                    print(" ✓")
                    break
                elif "Restarting" in status:
                    print(".", end="", flush=True)
                elif "Exited" in status or "Dead" in status:
                    print(f"\nContainer is stopped (status: {status})")
                    print("Attempting to start container...")
                    compose_file = get_compose_file()
                    start_result = subprocess.run(
                        ["docker", "compose", "-f", str(compose_file), "start", "smite-panel"],
                        capture_output=True,
                        text=True
                    )
                    if start_result.returncode == 0:
                        print("Container started. Waiting...")
                        time.sleep(5)
                        continue
                    else:
                        print(f"Failed to start container: {start_result.stderr}")
                        sys.exit(1)
                else:
                    print(f"\nContainer status: {status}")
                    print("Waiting for container to be ready...")
            print(".", end="", flush=True)
            time.sleep(2)
            waited += 2
        else:
            print("\nTimeout waiting for container to be ready.")
            print("Please check container status: docker ps -a | grep smite-panel")
            sys.exit(1)
        
        if container_name:
            print(f"Updating admin password via Docker container ({container_name})...")
            
            password_repr = repr(password)
            
            script_content = f"""import asyncio
import sys
import os
sys.path.insert(0, '/app')
from app.database import AsyncSessionLocal, init_db
from app.models import Admin
from sqlalchemy import select
from passlib.context import CryptContext

password = {password_repr}

if isinstance(password, str):
    password_bytes = password.encode('utf-8')
    if len(password_bytes) > 72:
        password = password_bytes[:72].decode('utf-8', errors='ignore')

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def update():
    await init_db()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Admin))
        admin = result.scalar_one_or_none()
        if not admin:
            print("Error: No admin user found", file=sys.stderr)
            sys.exit(1)
        
        try:
            password_hash = pwd_context.hash(password)
        except Exception as e:
            print(f"Error hashing password: {{e}}", file=sys.stderr)
            sys.exit(1)
        
        admin.password_hash = password_hash
        await session.commit()
        print(f"Admin password updated successfully!")

asyncio.run(update())
"""
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as tmp_file:
                tmp_file.write(script_content)
                tmp_file_path = tmp_file.name
            
            try:
                copy_proc = subprocess.run(
                    ["docker", "cp", tmp_file_path, f"{container_name}:/tmp/update_admin.py"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if copy_proc.returncode != 0:
                    import base64
                    script_b64 = base64.b64encode(script_content.encode()).decode()
                    script_one_liner = f"PYTHONPATH=/app echo {script_b64} | base64 -d | python3"
                    proc = subprocess.run(
                        ["docker", "exec", "-e", "PYTHONPATH=/app", container_name, "sh", "-c", script_one_liner],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                else:
                    proc = subprocess.run(
                        ["docker", "exec", "-e", "PYTHONPATH=/app", container_name, "python", "/tmp/update_admin.py"],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
            finally:
                try:
                    os.unlink(tmp_file_path)
                except:
                    pass
            
            if proc.returncode == 0:
                print(proc.stdout)
                return
            else:
                error_msg = proc.stderr.strip() or proc.stdout.strip()
                print(f"Warning: Docker exec failed: {error_msg}")
                print("Checking container logs...")
                log_proc = subprocess.run(
                    ["docker", "logs", "--tail", "10", container_name],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if log_proc.returncode == 0:
                    print("\nContainer logs:")
                    print(log_proc.stdout)
                print("\nTrying local method...")
        else:
            print("Warning: Container not running or still restarting. Checking container status...")
            status_proc = subprocess.run(
                ["docker", "ps", "-a", "--filter", "name=smite-panel", "--format", "table {{.Names}}\\t{{.Status}}"],
                capture_output=True,
                text=True
            )
            if status_proc.returncode == 0:
                print(status_proc.stdout)
            print("\nTrying local method...")
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError) as e:
        print(f"Warning: Docker error: {e}")
        print("Trying local method...")
    
    try:
        possible_roots = [
            Path(__file__).parent.parent,
            Path("/opt/smite"),
            Path.cwd(),
        ]
        
        panel_path = None
        for root in possible_roots:
            test_path = root / "panel"
            if test_path.exists() and (test_path / "main.py").exists():
                panel_path = test_path
                break
        
        if not panel_path:
            print("Error: Panel directory not found")
            print(f"Searched in: {[str(p / 'panel') for p in possible_roots]}")
            sys.exit(1)
        
        sys.path.insert(0, str(panel_path))
        
        from app.database import AsyncSessionLocal, init_db
        from app.models import Admin
        from sqlalchemy import select
        from passlib.context import CryptContext
        import asyncio
        
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        
        async def update():
            await init_db()
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Admin))
                admin = result.scalar_one_or_none()
                if not admin:
                    print("Error: No admin user found")
                    return
                
                password_hash = pwd_context.hash(password)
                admin.password_hash = password_hash
                await session.commit()
                print("Admin password updated successfully!")
        
        asyncio.run(update())
        
    except ImportError:
        print("Error: Panel dependencies not installed and Docker method failed.")
        print("\nPlease either:")
        print("  1. Start the panel: docker compose up -d")
        print("  2. Install dependencies: pip install -r panel/requirements.txt")
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to update admin password: {e}")
        sys.exit(1)


def cmd_status(args):
    """Show system status"""
    print("Panel Status:")
    print("-" * 50)
    
    result = subprocess.run(["docker", "ps", "--filter", "name=smite-panel", "--format", "{{.Status}}"], 
                          capture_output=True, text=True)
    if result.stdout.strip():
        print(f"Docker: {result.stdout.strip()}")
    else:
        print("Docker: Not running")
    
    try:
        panel_url = get_panel_url()
        
        if HAS_REQUESTS:
            response = requests.get(f"{panel_url}/api/status", timeout=2)
            if response.status_code == 200:
                data = response.json()
                print(f"API: Running")
                print(f"Nodes: {data['nodes']['active']}/{data['nodes']['total']} active")
                print(f"Tunnels: {data['tunnels']['active']}/{data['tunnels']['total']} active")
            else:
                print("API: Not responding")
        else:
            req = urllib.request.Request(f"{panel_url}/api/status")
            with urllib.request.urlopen(req, timeout=2) as response:
                data = json_lib.loads(response.read().decode())
                print(f"API: Running")
                print(f"Nodes: {data['nodes']['active']}/{data['nodes']['total']} active")
                print(f"Tunnels: {data['tunnels']['active']}/{data['tunnels']['total']} active")
    except Exception as e:
        print(f"API: Not accessible ({e})")


def cmd_update(args):
    """Update panel (pull images and recreate)"""
    print("Updating panel...")
    compose_file = get_compose_file()
    project_root = compose_file.parent
    
    if (project_root / ".git").exists():
        print("Pulling latest changes from git...")
        subprocess.run(["git", "pull"], cwd=project_root, check=False)
        if (project_root / "docker-compose.yml").exists():
            print("docker-compose.yml updated from git")
    
    run_docker_compose(["pull"])
    run_docker_compose(["up", "-d", "--force-recreate"])
    print("Panel updated.")


def cmd_restart(args):
    """Restart panel (recreate container to pick up .env changes, no pull)"""
    print("Restarting panel...")
    run_docker_compose(["stop", "smite-panel"])
    run_docker_compose(["rm", "-f", "smite-panel"])
    run_docker_compose(["up", "-d", "--no-deps", "smite-panel"])
    
    import time
    time.sleep(2)
    result = subprocess.run(["docker", "ps", "--filter", "name=smite-panel", "--format", "{{.Status}}"], capture_output=True, text=True)
    if not result.stdout.strip() or "Up" not in result.stdout:
        print("Warning: Panel container may not be running. Check logs with: docker logs smite-panel")
    
    result = subprocess.run(["docker", "ps", "--filter", "name=smite-nginx", "--format", "{{.Names}}"], capture_output=True, text=True)
    if result.stdout.strip():
        print("Restarting nginx...")
        run_docker_compose(["stop", "nginx"], profile="https")
        run_docker_compose(["rm", "-f", "nginx"], profile="https")
        run_docker_compose(["up", "-d", "--no-deps", "nginx"], profile="https")
    
    print("Panel restarted. Tunnels are preserved.")


def cmd_edit(args):
    """Edit docker-compose.yml"""
    compose_file = get_compose_file()
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(compose_file)])


def cmd_edit_env(args):
    """Edit .env file"""
    env_file = get_env_file()
    if not env_file.exists():
        print(f".env file not found. Creating from .env.example...")
        example_file = env_file.parent / ".env.example"
        if example_file.exists():
            env_file.write_text(example_file.read_text())
        else:
            env_file.write_text("")
    
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(env_file)])


def cmd_logs(args):
    """Stream logs"""
    follow = ["--follow"] if args.follow else []
    run_docker_compose(["logs"] + follow + ["smite-panel"])


def cmd_uninstall(args):
    """Uninstall Smite Panel - removes everything"""
    print("=" * 60)
    print("⚠️  WARNING: This will completely remove Smite Panel!")
    print("=" * 60)
    print("\nThis will remove:")
    print("  - All Docker containers (smite-panel, smite-nginx)")
    print("  - All Docker volumes")
    print("  - Installation directory (/opt/smite)")
    print("  - CLI script (/usr/local/bin/smite)")
    print("  - Crontab entries related to smite")
    print("  - Docker images (ghcr.io/zzedix/smite-panel, ghcr.io/zzedix/smite-nginx)")
    print("\n⚠️  ALL DATA WILL BE LOST!")
    print("=" * 60)
    
    response = input("\nAre you sure you want to continue? Type 'yes' to confirm: ")
    if response.lower() != 'yes':
        print("Uninstall cancelled.")
        sys.exit(0)
    
    print("\nStarting uninstall...")
    
    print("\n[1/6] Stopping and removing containers...")
    try:
        compose_file = get_compose_file()
        if compose_file.exists():
            compose_dir = compose_file.parent
            original_cwd = Path.cwd()
            try:
                os.chdir(compose_dir)
                subprocess.run(["docker", "compose", "-f", str(compose_file), "down", "-v"], 
                             capture_output=True, check=False)
                subprocess.run(["docker", "compose", "-f", str(compose_file), "--profile", "https", "down", "-v"], 
                             capture_output=True, check=False)
            finally:
                os.chdir(original_cwd)
        
        for container in ["smite-panel", "smite-nginx"]:
            subprocess.run(["docker", "stop", container], capture_output=True, check=False)
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
        print("  ✓ Containers removed")
    except Exception as e:
        print(f"  ⚠️  Warning: {e}")
    
    print("\n[2/6] Removing Docker volumes...")
    try:
        result = subprocess.run(["docker", "volume", "ls", "-q", "--filter", "name=smite"], 
                              capture_output=True, text=True)
        volumes = result.stdout.strip().split('\n')
        for volume in volumes:
            if volume:
                subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, check=False)
        print("  ✓ Volumes removed")
    except Exception as e:
        print(f"  ⚠️  Warning: {e}")
    
    print("\n[3/6] Removing Docker images...")
    try:
        for image in ["ghcr.io/zzedix/smite-panel", "ghcr.io/zzedix/smite-nginx"]:
            subprocess.run(["docker", "rmi", "-f", image], capture_output=True, check=False)
            subprocess.run(["docker", "rmi", "-f", f"{image}:latest"], capture_output=True, check=False)
            result = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", image], 
                                  capture_output=True, text=True)
            for tag in result.stdout.strip().split('\n'):
                if tag:
                    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)
        print("  ✓ Images removed")
    except Exception as e:
        print(f"  ⚠️  Warning: {e}")
    
    print("\n[4/6] Removing installation directory...")
    install_dirs = [Path("/opt/smite")]
    for install_dir in install_dirs:
        if install_dir.exists():
            try:
                shutil.rmtree(install_dir)
                print(f"  ✓ Removed {install_dir}")
            except Exception as e:
                print(f"  ⚠️  Warning: Could not remove {install_dir}: {e}")
        else:
            print(f"  - {install_dir} does not exist")
    
    print("\n[5/6] Removing CLI script...")
    cli_path = Path("/usr/local/bin/smite")
    if cli_path.exists():
        try:
            cli_path.unlink()
            print("  ✓ Removed /usr/local/bin/smite")
        except Exception as e:
            print(f"  ⚠️  Warning: Could not remove CLI script: {e}")
    else:
        print("  - CLI script not found")
    
    print("\n[6/6] Removing crontab entries...")
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            new_lines = [line for line in lines if "smite" not in line.lower() and "certbot" not in line.lower()]
            if len(new_lines) != len(lines):
                new_crontab = "\n".join(new_lines) + "\n" if new_lines else ""
                subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=False)
                print("  ✓ Removed crontab entries")
            else:
                print("  - No crontab entries found")
        else:
            print("  - No crontab found")
    except Exception as e:
        print(f"  ⚠️  Warning: Could not modify crontab: {e}")
    
    print("\n" + "=" * 60)
    print("✅ Smite Panel has been completely uninstalled!")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Smite Panel CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    admin_parser = subparsers.add_parser("admin", help="Admin management")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_action")
    create_parser = admin_subparsers.add_parser("create", help="Create admin user")
    create_parser.add_argument("--username", help="Username")
    create_parser.add_argument("--password", help="Password")
    update_parser = admin_subparsers.add_parser("update", help="Update admin password")
    update_parser.add_argument("--password", help="Password")
    
    subparsers.add_parser("status", help="Show system status")
    
    subparsers.add_parser("update", help="Update panel (pull images and recreate)")
    
    subparsers.add_parser("restart", help="Restart panel (recreate to pick up .env changes)")
    
    subparsers.add_parser("edit", help="Edit docker-compose.yml")
    
    subparsers.add_parser("edit-env", help="Edit .env file")
    
    logs_parser = subparsers.add_parser("logs", help="View logs")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="Follow logs")
    
    subparsers.add_parser("uninstall", help="Completely remove Smite Panel")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    if args.command == "admin":
        if args.admin_action == "create":
            cmd_admin_create(args)
        elif args.admin_action == "update":
            cmd_admin_update(args)
        else:
            admin_parser.print_help()
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "restart":
        cmd_restart(args)
    elif args.command == "edit":
        cmd_edit(args)
    elif args.command == "edit-env":
        cmd_edit_env(args)
    elif args.command == "logs":
        cmd_logs(args)
    elif args.command == "uninstall":
        cmd_uninstall(args)


if __name__ == "__main__":
    main()
