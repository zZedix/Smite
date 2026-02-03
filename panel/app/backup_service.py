"""Backup and restore service for panel data"""
import asyncio
import json
import hashlib
import logging
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import select, text
from app.database import AsyncSessionLocal
from app.models import Node, Tunnel, Admin, Settings

logger = logging.getLogger(__name__)


class BackupService:
    """Service for creating and restoring panel backups"""
    
    def __init__(self):
        self._panel_uuid: Optional[str] = None
    
    async def get_panel_uuid(self) -> str:
        """Get or create unique panel identifier"""
        if self._panel_uuid:
            return self._panel_uuid
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Settings).where(Settings.key == "panel_uuid")
            )
            setting = result.scalar_one_or_none()
            
            if setting and setting.value:
                self._panel_uuid = setting.value.get("uuid")
            
            if not self._panel_uuid:
                # Generate new UUID
                self._panel_uuid = str(uuid.uuid4())
                if setting:
                    setting.value = {"uuid": self._panel_uuid}
                else:
                    new_setting = Settings(
                        key="panel_uuid",
                        value={"uuid": self._panel_uuid}
                    )
                    session.add(new_setting)
                await session.commit()
        
        return self._panel_uuid
    
    async def get_panel_info(self) -> Dict[str, Any]:
        """Get current panel information for backup metadata"""
        async with AsyncSessionLocal() as session:
            # Count nodes
            nodes_result = await session.execute(select(Node))
            nodes = nodes_result.scalars().all()
            
            # Count tunnels
            tunnels_result = await session.execute(select(Tunnel))
            tunnels = tunnels_result.scalars().all()
            
            # Count admins
            admins_result = await session.execute(select(Admin))
            admins = admins_result.scalars().all()
        
        from app.config import settings
        
        return {
            "panel_uuid": await self.get_panel_uuid(),
            "smite_version": os.getenv("SMITE_VERSION", "unknown"),
            "panel_domain": settings.panel_domain or "localhost",
            "https_enabled": settings.https_enabled,
            "node_count": len(nodes),
            "tunnel_count": len(tunnels),
            "admin_count": len(admins),
            "active_nodes": sum(1 for n in nodes if n.status == "active"),
            "active_tunnels": sum(1 for t in tunnels if t.status == "active")
        }
    
    def _find_panel_root(self) -> Path:
        """Find panel root directory"""
        # Try common locations
        candidates = [
            Path("/opt/smite"),
            Path("/app"),
            Path(os.getcwd()),
            Path(__file__).parent.parent
        ]
        
        for candidate in candidates:
            if (candidate / "panel" / "data").exists():
                return candidate / "panel"
            if (candidate / "data").exists():
                return candidate
        
        return Path(os.getcwd())
    
    def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of a file"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return f"sha256:{sha256_hash.hexdigest()}"
    
    async def create_backup(self, output_path: Optional[str] = None) -> Optional[str]:
        """
        Create backup archive containing all panel data.
        
        Returns path to created backup file, or None on failure.
        """
        try:
            from app.config import settings
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_dir = Path(f"/tmp/smite_backup_{timestamp}")
            backup_dir.mkdir(exist_ok=True)
            
            panel_root = self._find_panel_root()
            checksums: Dict[str, str] = {}
            
            # 1. Backup data directory (includes SQLite database)
            data_dir = panel_root / "data"
            if data_dir.exists():
                shutil.copytree(data_dir, backup_dir / "data", dirs_exist_ok=True)
                # Calculate checksum for database
                db_path = backup_dir / "data" / "smite.db"
                if db_path.exists():
                    checksums["data/smite.db"] = self._calculate_checksum(db_path)
                logger.info(f"Backed up data folder from: {data_dir}")
            
            # 2. Backup certificates
            certs_dir = panel_root / "certs"
            if certs_dir.exists():
                shutil.copytree(certs_dir, backup_dir / "certs", dirs_exist_ok=True)
                for cert_file in certs_dir.glob("*"):
                    if cert_file.is_file():
                        checksums[f"certs/{cert_file.name}"] = self._calculate_checksum(cert_file)
                logger.info(f"Backed up certs folder from: {certs_dir}")
            
            # 3. Backup configuration files
            (backup_dir / "config").mkdir(exist_ok=True)
            
            # Find and backup .env
            env_locations = [
                Path("/app/config/.env"),
                panel_root.parent / ".env",
                Path("/opt/smite/.env"),
                Path(os.getcwd()) / ".env"
            ]
            
            for env_path in env_locations:
                if env_path.exists():
                    shutil.copy2(env_path, backup_dir / "config" / "env")
                    logger.info(f"Backed up .env from: {env_path}")
                    break
            
            # Find and backup docker-compose.yml
            compose_locations = [
                Path("/app/config/docker-compose.yml"),
                panel_root.parent / "docker-compose.yml",
                Path("/opt/smite/docker-compose.yml"),
                Path(os.getcwd()) / "docker-compose.yml"
            ]
            
            for compose_path in compose_locations:
                if compose_path.exists():
                    shutil.copy2(compose_path, backup_dir / "config" / "docker-compose.yml")
                    logger.info(f"Backed up docker-compose.yml from: {compose_path}")
                    break
            
            # 4. Backup Let's Encrypt certs if HTTPS enabled
            if settings.https_enabled and settings.panel_domain:
                letsencrypt_dir = Path("/etc/letsencrypt")
                if letsencrypt_dir.exists():
                    domain_dir = letsencrypt_dir / "live" / settings.panel_domain
                    if domain_dir.exists():
                        le_backup = backup_dir / "letsencrypt" / "live" / settings.panel_domain
                        le_backup.mkdir(parents=True, exist_ok=True)
                        for cert_file in ["fullchain.pem", "privkey.pem", "chain.pem", "cert.pem"]:
                            cert_path = domain_dir / cert_file
                            if cert_path.exists():
                                shutil.copy2(cert_path, le_backup / cert_file)
                        logger.info(f"Backed up Let's Encrypt certs from: {domain_dir}")
                
                # Backup nginx config
                nginx_dir = panel_root.parent / "nginx"
                if nginx_dir.exists():
                    shutil.copytree(nginx_dir, backup_dir / "nginx", dirs_exist_ok=True)
            
            # 5. Create manifest
            panel_info = await self.get_panel_info()
            manifest = {
                "version": "1.0",
                "smite_version": panel_info["smite_version"],
                "panel_uuid": panel_info["panel_uuid"],
                "created_at": datetime.utcnow().isoformat() + "Z",
                "panel_domain": panel_info["panel_domain"],
                "https_enabled": panel_info["https_enabled"],
                "node_count": panel_info["node_count"],
                "tunnel_count": panel_info["tunnel_count"],
                "admin_count": panel_info["admin_count"],
                "checksums": checksums
            }
            
            with open(backup_dir / "manifest.json", "w") as f:
                json.dump(manifest, f, indent=2)
            
            # 6. Create ZIP archive
            if output_path:
                backup_file = output_path
            else:
                backup_file = f"/tmp/smite_backup_{timestamp}.zip"
            
            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(backup_dir):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(backup_dir)
                        zipf.write(file_path, arcname)
            
            # Cleanup temp directory
            shutil.rmtree(backup_dir)
            
            logger.info(f"Backup created successfully: {backup_file}")
            return backup_file
            
        except Exception as e:
            logger.error(f"Error creating backup: {e}", exc_info=True)
            return None
    
    def inspect_backup(self, backup_path: str) -> Optional[Dict[str, Any]]:
        """
        Inspect backup file and return metadata without extracting.
        
        Returns manifest data or None if invalid.
        """
        try:
            with zipfile.ZipFile(backup_path, 'r') as zipf:
                if "manifest.json" not in zipf.namelist():
                    return {"error": "Invalid backup: missing manifest.json"}
                
                with zipf.open("manifest.json") as f:
                    manifest = json.load(f)
                
                # Add file list info
                manifest["files"] = zipf.namelist()
                manifest["has_database"] = "data/smite.db" in zipf.namelist()
                manifest["has_certs"] = any(f.startswith("certs/") for f in zipf.namelist())
                manifest["has_config"] = any(f.startswith("config/") for f in zipf.namelist())
                
                return manifest
                
        except zipfile.BadZipFile:
            return {"error": "Invalid backup: not a valid ZIP file"}
        except json.JSONDecodeError:
            return {"error": "Invalid backup: malformed manifest.json"}
        except Exception as e:
            return {"error": f"Failed to inspect backup: {str(e)}"}
    
    async def restore_backup(
        self,
        backup_path: str,
        create_pre_restore_backup: bool = True
    ) -> Dict[str, Any]:
        """
        Restore panel from backup file.
        
        This is a DESTRUCTIVE operation that replaces all current data.
        
        Returns dict with status and details.
        """
        try:
            # 1. Validate backup
            manifest = self.inspect_backup(backup_path)
            if not manifest or "error" in manifest:
                return {"success": False, "error": manifest.get("error", "Invalid backup")}
            
            if not manifest.get("has_database"):
                return {"success": False, "error": "Backup does not contain database"}
            
            panel_root = self._find_panel_root()
            
            # 2. Create pre-restore backup for safety
            pre_restore_path = None
            if create_pre_restore_backup:
                pre_restore_path = await self.create_backup(
                    output_path=f"/tmp/smite_pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                )
                if pre_restore_path:
                    logger.info(f"Created pre-restore backup: {pre_restore_path}")
            
            # 3. Extract backup to temp directory
            extract_dir = Path(f"/tmp/smite_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            with zipfile.ZipFile(backup_path, 'r') as zipf:
                zipf.extractall(extract_dir)
            
            try:
                # 4. Restore data directory
                if (extract_dir / "data").exists():
                    data_dir = panel_root / "data"
                    if data_dir.exists():
                        # Backup current db before overwriting
                        shutil.rmtree(data_dir)
                    shutil.copytree(extract_dir / "data", data_dir)
                    logger.info("Restored data directory")
                
                # 5. Restore certificates
                if (extract_dir / "certs").exists():
                    certs_dir = panel_root / "certs"
                    if certs_dir.exists():
                        shutil.rmtree(certs_dir)
                    shutil.copytree(extract_dir / "certs", certs_dir)
                    logger.info("Restored certificates")
                
                # 6. Restore .env (to config mount point or parent directory)
                env_backup = extract_dir / "config" / "env"
                if env_backup.exists():
                    for env_target in [
                        Path("/app/config/.env"),
                        panel_root.parent / ".env",
                        Path("/opt/smite/.env")
                    ]:
                        if env_target.parent.exists():
                            shutil.copy2(env_backup, env_target)
                            logger.info(f"Restored .env to: {env_target}")
                            break
                
                # 7. Restore docker-compose.yml
                compose_backup = extract_dir / "config" / "docker-compose.yml"
                if compose_backup.exists():
                    for compose_target in [
                        Path("/app/config/docker-compose.yml"),
                        panel_root.parent / "docker-compose.yml",
                        Path("/opt/smite/docker-compose.yml")
                    ]:
                        if compose_target.parent.exists():
                            shutil.copy2(compose_backup, compose_target)
                            logger.info(f"Restored docker-compose.yml to: {compose_target}")
                            break
                
                # 8. Restore Let's Encrypt certs if present
                le_backup = extract_dir / "letsencrypt"
                if le_backup.exists():
                    le_target = Path("/etc/letsencrypt")
                    if le_target.exists():
                        for domain_dir in (le_backup / "live").iterdir():
                            target_domain = le_target / "live" / domain_dir.name
                            target_domain.mkdir(parents=True, exist_ok=True)
                            for cert_file in domain_dir.iterdir():
                                shutil.copy2(cert_file, target_domain / cert_file.name)
                        logger.info("Restored Let's Encrypt certificates")
                
                # 9. Clear panel UUID cache to reload from restored database
                self._panel_uuid = None
                
                logger.info("Restore completed successfully")
                
                return {
                    "success": True,
                    "message": "Backup restored successfully",
                    "restored_from": manifest.get("created_at"),
                    "original_panel_uuid": manifest.get("panel_uuid"),
                    "node_count": manifest.get("node_count"),
                    "tunnel_count": manifest.get("tunnel_count"),
                    "pre_restore_backup": pre_restore_path
                }
                
            except Exception as e:
                logger.error(f"Restore failed, attempting rollback: {e}")
                # Attempt to restore from pre-restore backup
                if pre_restore_path:
                    try:
                        await self.restore_backup(pre_restore_path, create_pre_restore_backup=False)
                        return {
                            "success": False,
                            "error": f"Restore failed: {str(e)}. Rolled back to previous state."
                        }
                except Exception:
                    pass
                return {"success": False, "error": f"Restore failed: {str(e)}"}
            
            finally:
                # Cleanup extract directory
                if extract_dir.exists():
                    shutil.rmtree(extract_dir)
                    
        except Exception as e:
            logger.error(f"Error restoring backup: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


# Global instance
backup_service = BackupService()
