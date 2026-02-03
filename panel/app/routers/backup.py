"""Backup and restore API endpoints"""
import os
import logging
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.backup_service import backup_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.get("/panel-info")
async def get_panel_info():
    """Get current panel information (UUID, version, stats)"""
    try:
        info = await backup_service.get_panel_info()
        return info
    except Exception as e:
        logger.error(f"Failed to get panel info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create")
async def create_backup():
    """
    Create a backup and return it as a downloadable file.
    
    The backup includes:
    - Database (nodes, tunnels, admins, settings)
    - CA certificates (for node authentication)
    - Environment configuration
    - Let's Encrypt certificates (if HTTPS enabled)
    """
    try:
        backup_path = await backup_service.create_backup()
        
        if not backup_path:
            raise HTTPException(status_code=500, detail="Failed to create backup")
        
        if not os.path.exists(backup_path):
            raise HTTPException(status_code=500, detail="Backup file not found")
        
        # Get filename from path
        filename = os.path.basename(backup_path)
        
        return FileResponse(
            path=backup_path,
            filename=filename,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create backup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/inspect")
async def inspect_backup(file: UploadFile = File(...)):
    """
    Inspect a backup file and return its metadata without restoring.
    
    Use this to preview backup contents before deciding to restore.
    """
    try:
        # Save uploaded file temporarily
        temp_path = f"/tmp/smite_inspect_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        try:
            manifest = backup_service.inspect_backup(temp_path)
            
            if not manifest:
                raise HTTPException(status_code=400, detail="Failed to inspect backup")
            
            if "error" in manifest:
                raise HTTPException(status_code=400, detail=manifest["error"])
            
            return manifest
            
        finally:
            # Cleanup temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to inspect backup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    confirm: bool = False
):
    """
    Restore panel from a backup file.
    
    ⚠️ WARNING: This is a DESTRUCTIVE operation!
    
    This will:
    - REPLACE all current data (nodes, tunnels, settings)
    - OVERWRITE CA certificates
    - INVALIDATE all current login sessions
    
    A pre-restore backup is automatically created for safety.
    
    Parameters:
    - file: The backup ZIP file to restore from
    - confirm: Must be True to proceed with restore
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Restore requires explicit confirmation. Set confirm=true to proceed."
        )
    
    try:
        # Save uploaded file temporarily
        temp_path = f"/tmp/smite_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        try:
            # First inspect to validate
            manifest = backup_service.inspect_backup(temp_path)
            
            if not manifest:
                raise HTTPException(status_code=400, detail="Invalid backup file")
            
            if "error" in manifest:
                raise HTTPException(status_code=400, detail=manifest["error"])
            
            # Log the restore attempt
            logger.warning(
                f"RESTORE INITIATED: Restoring from backup created at {manifest.get('created_at')} "
                f"(original panel: {manifest.get('panel_uuid')})"
            )
            
            # Perform restore
            result = await backup_service.restore_backup(temp_path)
            
            if not result.get("success"):
                raise HTTPException(
                    status_code=500,
                    detail=result.get("error", "Restore failed")
                )
            
            logger.info(f"RESTORE COMPLETED: {result}")
            
            return {
                "success": True,
                "message": "Backup restored successfully. Please refresh the page and log in again.",
                "details": result
            }
            
        finally:
            # Cleanup temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to restore backup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
