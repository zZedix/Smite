"""
Smite Panel - Central Controller
"""
import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Tunnel, Node, CoreResetConfig

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import nodes, tunnels, panel, status, logs, auth, core_health, backup
from app.routers import settings as settings_router
from app.node_server import NodeServer
from app.gost_forwarder import gost_forwarder
from app.rathole_server import rathole_server_manager
from app.backhaul_manager import backhaul_manager
from app.chisel_server import chisel_server_manager
from app.frp_server import frp_server_manager
from app.frp_comm_manager import frp_comm_manager
from app.telegram_bot import telegram_bot
from app.node_client import NodeClient
from app.models import Settings
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    await init_db()
    
    h2_server = NodeServer()
    await h2_server.start()
    app.state.h2_server = h2_server
    
    try:
        cert_path = Path(settings.node_cert_path)
        if not cert_path.is_absolute():
            cert_path = Path(os.getcwd()) / cert_path
        
        if not cert_path.exists() or cert_path.stat().st_size == 0:
            logger.info("Generating CA certificate for Iran nodes on startup...")
            h2_server.cert_path = str(cert_path)
            h2_server.key_path = str(cert_path.parent / "ca.key")
            await h2_server._generate_certs(common_name="Smite CA")
            logger.info(f"CA certificate generated at {cert_path}")
    except Exception as e:
        logger.warning(f"Failed to generate CA certificate on startup: {e}")
    
    try:
        server_cert_path = Path(settings.node_server_cert_path)
        if not server_cert_path.is_absolute():
            server_cert_path = Path(os.getcwd()) / server_cert_path
        
        if not server_cert_path.exists() or server_cert_path.stat().st_size == 0:
            logger.info("Generating CA certificate for foreign servers on startup...")
            h2_server.cert_path = str(server_cert_path)
            h2_server.key_path = str(server_cert_path.parent / "ca-server.key")
            await h2_server._generate_certs(common_name="Smite Server CA")
            logger.info(f"Server CA certificate generated at {server_cert_path}")
    except Exception as e:
        logger.warning(f"Failed to generate server CA certificate on startup: {e}")
    
    app.state.gost_forwarder = gost_forwarder
    
    app.state.rathole_server_manager = rathole_server_manager
    app.state.backhaul_manager = backhaul_manager
    app.state.chisel_server_manager = chisel_server_manager
    app.state.frp_server_manager = frp_server_manager
    app.state.frp_comm_manager = frp_comm_manager
    
    await _load_and_start_frp_comm()
    await _load_and_start_telegram_bot()
    await _load_and_start_tunnel_reapply()
    
    await _restore_forwards()
    
    await _restore_node_tunnels()
    
    reset_task = asyncio.create_task(_auto_reset_scheduler(app))
    app.state.reset_task = reset_task
    
    yield
    
    if hasattr(app.state, 'reset_task'):
        app.state.reset_task.cancel()
        try:
            await app.state.reset_task
        except asyncio.CancelledError:
            pass
    
    if hasattr(app.state, 'h2_server'):
        await app.state.h2_server.stop()
    
    if hasattr(app.state, 'frp_comm_manager'):
        app.state.frp_comm_manager.stop()
    
    await telegram_bot.stop()
    
    gost_forwarder.cleanup_all()


async def _restore_forwards():
    """Restore forwarding for active tunnels on startup"""
    try:
        logger.info("Starting to restore forwarding for active tunnels...")
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            logger.info(f"Found {len(tunnels)} active tunnels to restore")
            
            for tunnel in tunnels:
                logger.info(f"Checking tunnel {tunnel.id}: type={tunnel.type}, core={tunnel.core}, node_id={tunnel.node_id}")
                needs_gost_forwarding = tunnel.type in ["tcp", "udp", "ws", "grpc", "tcpmux"] and tunnel.core == "gost" and not tunnel.node_id
                if not needs_gost_forwarding:
                    continue
                
                listen_port = tunnel.spec.get("listen_port")
                forward_to = tunnel.spec.get("forward_to")
                
                if not forward_to:
                    remote_ip = tunnel.spec.get("remote_ip", "127.0.0.1")
                    remote_port = tunnel.spec.get("remote_port", 8080)
                    forward_to = f"{remote_ip}:{remote_port}"
                
                panel_port = listen_port or tunnel.spec.get("remote_port")
                if not panel_port or not forward_to:
                    logger.warning(f"Tunnel {tunnel.id}: Missing panel_port or forward_to, skipping restore")
                    continue
                
                try:
                    use_ipv6 = tunnel.spec.get("use_ipv6", False)
                    logger.info(f"Restoring gost forwarding for tunnel {tunnel.id}: {tunnel.type}://:{panel_port} -> {forward_to}, use_ipv6={use_ipv6}")
                    gost_forwarder.start_forward(
                        tunnel_id=tunnel.id,
                        local_port=int(panel_port),
                        forward_to=forward_to,
                        tunnel_type=tunnel.type,
                        use_ipv6=bool(use_ipv6)
                    )
                    logger.info(f"Successfully restored gost forwarding for tunnel {tunnel.id}")
                except Exception as e:
                    logger.error(f"Failed to restore forwarding for tunnel {tunnel.id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error restoring forwards: {e}")


async def _restore_rathole_servers():
    """Restore Rathole servers for active tunnels on startup"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            for tunnel in tunnels:
                if tunnel.core != "rathole":
                    continue
                
                remote_addr = tunnel.spec.get("remote_addr")
                token = tunnel.spec.get("token")
                proxy_port = tunnel.spec.get("remote_port") or tunnel.spec.get("listen_port")
                
                if not remote_addr or not token or not proxy_port:
                    continue
                
                use_ipv6 = tunnel.spec.get("use_ipv6", False)
                rathole_server_manager.start_server(
                    tunnel_id=tunnel.id,
                    remote_addr=remote_addr,
                    token=token,
                    proxy_port=int(proxy_port),
                    use_ipv6=bool(use_ipv6)
                )
    except Exception as e:
        logger.error(f"Error restoring Rathole servers: {e}")


async def _restore_backhaul_servers():
    """Restore Backhaul servers for active tunnels on startup"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()

            for tunnel in tunnels:
                if tunnel.core != "backhaul":
                    continue

                try:
                    backhaul_manager.start_server(tunnel.id, tunnel.spec or {})
                except Exception as exc:
                    logger.error(
                        "Failed to restore Backhaul server for tunnel %s: %s",
                        tunnel.id,
                        exc,
                    )
    except Exception as exc:
        logger.error("Error restoring Backhaul servers: %s", exc)


async def _restore_chisel_servers():
    """Restore Chisel servers for active tunnels on startup"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            for tunnel in tunnels:
                if tunnel.core != "chisel":
                    continue
                
                listen_port = tunnel.spec.get("listen_port") or tunnel.spec.get("remote_port") or tunnel.spec.get("server_port")
                auth = tunnel.spec.get("auth")
                fingerprint = tunnel.spec.get("fingerprint")
                
                if not listen_port:
                    continue
                
                try:
                    use_ipv6 = tunnel.spec.get("use_ipv6", False)
                    server_control_port = tunnel.spec.get("control_port")
                    if server_control_port:
                        server_control_port = int(server_control_port)
                    else:
                        server_control_port = int(listen_port) + 10000
                    chisel_server_manager.start_server(
                        tunnel_id=tunnel.id,
                        server_port=server_control_port,
                        auth=auth,
                        fingerprint=fingerprint,
                        use_ipv6=bool(use_ipv6)
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to restore Chisel server for tunnel %s: %s",
                        tunnel.id,
                        exc,
                    )
    except Exception as exc:
        logger.error("Error restoring Chisel servers: %s", exc)


async def _restore_frp_servers():
    """Restore FRP servers for active tunnels on startup"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            for tunnel in tunnels:
                if tunnel.core != "frp":
                    continue
                
                bind_port = tunnel.spec.get("bind_port", 7000)
                token = tunnel.spec.get("token")
                
                if not bind_port:
                    continue
                
                try:
                    frp_server_manager.start_server(
                        tunnel_id=tunnel.id,
                        bind_port=int(bind_port),
                        token=token
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to restore FRP server for tunnel %s: %s",
                        tunnel.id,
                        exc,
                    )
    except Exception as exc:
        logger.error("Error restoring FRP servers: %s", exc)


async def _restore_node_tunnels():
    """Sync node-side tunnels with panel database after panel restart
    
    Note: Nodes restore their own tunnels on startup independently.
    This function syncs the panel's view with nodes, but tunnels will
    continue working even if panel is down or this sync fails.
    """
    try:
        logger.info("Starting to sync node-side tunnels with panel database...")
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            logger.info(f"Found {len(tunnels)} active tunnels to check for sync")
            
            reverse_tunnels = [t for t in tunnels if t.core in ["rathole", "backhaul", "chisel", "frp"]]
            gost_tunnels = [t for t in tunnels if t.core == "gost" and t.node_id]
            
            if not reverse_tunnels and not gost_tunnels:
                logger.info("No node-side tunnels to sync")
                return
            
            logger.info(f"Found {len(reverse_tunnels)} active reverse tunnels and {len(gost_tunnels)} node-side GOST tunnels to sync")
            
            client = NodeClient()
            restored_count = 0
            failed_count = 0
            skipped_count = 0
            
            for tunnel in reverse_tunnels:
                try:
                    iran_node = None
                    foreign_node = None
                    
                    if tunnel.node_id:
                        result = await db.execute(select(Node).where(Node.id == tunnel.node_id))
                        iran_node = result.scalar_one_or_none()
                        if iran_node and iran_node.node_metadata.get("role") != "iran":
                            foreign_node = iran_node
                            iran_node = None
                    
                    if not foreign_node:
                        result = await db.execute(select(Node))
                        all_nodes = result.scalars().all()
                        foreign_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"]
                        if foreign_nodes:
                            foreign_node = foreign_nodes[0]
                    
                    if not iran_node:
                        if tunnel.node_id:
                            result = await db.execute(select(Node).where(Node.id == tunnel.node_id))
                            iran_node = result.scalar_one_or_none()
                        if not iran_node:
                            result = await db.execute(select(Node))
                            all_nodes = result.scalars().all()
                            iran_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "iran"]
                            if iran_nodes:
                                iran_node = iran_nodes[0]
                    
                    if not foreign_node or not iran_node:
                        logger.warning(f"Tunnel {tunnel.id}: Missing foreign or iran node, skipping sync (nodes will restore themselves)")
                        skipped_count += 1
                        continue
                    
                    server_spec = tunnel.spec.copy() if tunnel.spec else {}
                    server_spec["mode"] = "server"
                    
                    client_spec = tunnel.spec.copy() if tunnel.spec else {}
                    client_spec["mode"] = "client"
                    
                    if tunnel.core == "rathole":
                        transport = server_spec.get("transport") or server_spec.get("type") or "tcp"
                        proxy_port = server_spec.get("remote_port") or server_spec.get("listen_port")
                        token = server_spec.get("token")
                        if not proxy_port or not token:
                            logger.warning(f"Tunnel {tunnel.id}: Missing remote_port or token, skipping")
                            continue
                        
                        remote_addr = server_spec.get("remote_addr", "0.0.0.0:23333")
                        from app.utils import parse_address_port
                        _, control_port, _ = parse_address_port(remote_addr)
                        if not control_port:
                            import hashlib
                            port_hash = int(hashlib.md5(tunnel.id.encode()).hexdigest()[:8], 16)
                            control_port = 23333 + (port_hash % 1000)  # Ports 23333-24332
                        server_spec["bind_addr"] = f"0.0.0.0:{control_port}"
                        server_spec["proxy_port"] = proxy_port
                        server_spec["transport"] = transport
                        server_spec["type"] = transport
                        if "websocket_tls" in server_spec:
                            server_spec["websocket_tls"] = server_spec["websocket_tls"]
                        elif "tls" in server_spec:
                            server_spec["websocket_tls"] = server_spec["tls"]
                        
                        iran_node_ip = iran_node.node_metadata.get("ip_address")
                        if not iran_node_ip:
                            logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping")
                            continue
                        transport_lower = transport.lower()
                        if transport_lower in ("websocket", "ws"):
                            use_tls = bool(server_spec.get("websocket_tls") or server_spec.get("tls"))
                            protocol = "wss://" if use_tls else "ws://"
                            client_spec["remote_addr"] = f"{protocol}{iran_node_ip}:{control_port}"
                        else:
                            client_spec["remote_addr"] = f"{iran_node_ip}:{control_port}"
                        client_spec["transport"] = transport
                        client_spec["type"] = transport
                        client_spec["token"] = token
                        if "websocket_tls" in server_spec:
                            client_spec["websocket_tls"] = server_spec["websocket_tls"]
                        elif "tls" in server_spec:
                            client_spec["websocket_tls"] = server_spec["tls"]
                        local_addr = client_spec.get("local_addr")
                        if not local_addr:
                            local_addr = f"{iran_node_ip}:{proxy_port}"
                        client_spec["local_addr"] = local_addr
                    
                    elif tunnel.core == "chisel":
                        listen_port = server_spec.get("listen_port") or server_spec.get("remote_port")
                        if not listen_port:
                            logger.warning(f"Tunnel {tunnel.id}: Missing listen_port, skipping")
                            continue
                        
                        iran_node_ip = iran_node.node_metadata.get("ip_address")
                        if not iran_node_ip:
                            logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping")
                            continue
                        import hashlib
                        port_hash = int(hashlib.md5(tunnel.id.encode()).hexdigest()[:8], 16)
                        server_control_port = server_spec.get("control_port") or (int(listen_port) + 10000 + (port_hash % 1000))
                        server_spec["server_port"] = server_control_port
                        server_spec["reverse_port"] = listen_port
                        auth = server_spec.get("auth")
                        if auth:
                            server_spec["auth"] = auth
                        fingerprint = server_spec.get("fingerprint")
                        if fingerprint:
                            server_spec["fingerprint"] = fingerprint
                        
                        client_spec["server_url"] = f"http://{iran_node_ip}:{server_control_port}"
                        client_spec["reverse_port"] = listen_port
                        if auth:
                            client_spec["auth"] = auth
                        if fingerprint:
                            client_spec["fingerprint"] = fingerprint
                        local_addr = client_spec.get("local_addr")
                        if not local_addr:
                            local_addr = f"{iran_node_ip}:{listen_port}"
                        client_spec["local_addr"] = local_addr
                    
                    elif tunnel.core == "frp":
                        bind_port = server_spec.get("bind_port", 7000)
                        token = server_spec.get("token")
                        server_spec["bind_port"] = bind_port
                        if token:
                            server_spec["token"] = token
                        
                        iran_node_ip = iran_node.node_metadata.get("ip_address")
                        if not iran_node_ip:
                            logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping")
                            continue
                        client_spec["server_addr"] = iran_node_ip
                        client_spec["server_port"] = bind_port
                        if token:
                            client_spec["token"] = token
                        tunnel_type = tunnel.type.lower() if tunnel.type else "tcp"
                        if tunnel_type not in ["tcp", "udp"]:
                            tunnel_type = "tcp"
                        client_spec["type"] = tunnel_type
                    
                    elif tunnel.core == "backhaul":
                        transport = server_spec.get("transport") or server_spec.get("type") or "tcp"
                        import hashlib
                        port_hash = int(hashlib.md5(tunnel.id.encode()).hexdigest()[:8], 16)
                        control_port = server_spec.get("control_port") or server_spec.get("listen_port") or (3080 + (port_hash % 1000))
                        public_port = server_spec.get("public_port") or server_spec.get("remote_port") or server_spec.get("listen_port")
                        target_host = server_spec.get("target_host", "127.0.0.1")
                        target_port = server_spec.get("target_port") or public_port
                        token = server_spec.get("token")
                        
                        if not public_port:
                            logger.warning(f"Tunnel {tunnel.id}: Missing public_port, skipping")
                            continue
                        
                        bind_ip = server_spec.get("bind_ip") or server_spec.get("listen_ip") or "0.0.0.0"
                        server_spec["bind_addr"] = f"{bind_ip}:{control_port}"
                        server_spec["transport"] = transport
                        server_spec["type"] = transport
                        if target_port:
                            target_addr = f"{target_host}:{target_port}"
                            server_spec["ports"] = [f"{public_port}={target_addr}"]
                        else:
                            server_spec["ports"] = [str(public_port)]
                        if token:
                            server_spec["token"] = token
                        
                        iran_node_ip = iran_node.node_metadata.get("ip_address")
                        if not iran_node_ip:
                            logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping")
                            continue
                        transport_lower = transport.lower()
                        if transport_lower in ("ws", "wsmux"):
                            use_tls = bool(server_spec.get("tls_cert") or server_spec.get("server_options", {}).get("tls_cert"))
                            protocol = "wss://" if use_tls else "ws://"
                            client_spec["remote_addr"] = f"{protocol}{iran_node_ip}:{control_port}"
                        else:
                            client_spec["remote_addr"] = f"{iran_node_ip}:{control_port}"
                        client_spec["transport"] = transport
                        client_spec["type"] = transport
                        if token:
                            client_spec["token"] = token
                    
                    if not iran_node.node_metadata.get("api_address"):
                        iran_node.node_metadata["api_address"] = f"http://{iran_node.node_metadata.get('ip_address', iran_node.fingerprint)}:{iran_node.node_metadata.get('api_port', 8888)}"
                        await db.commit()
                    
                    logger.info(f"Restoring tunnel {tunnel.id}: applying server config to iran node {iran_node.id}")
                    server_response = await client.send_to_node(
                        node_id=iran_node.id,
                        endpoint="/api/agent/tunnels/apply",
                        data={
                            "tunnel_id": tunnel.id,
                            "core": tunnel.core,
                            "type": tunnel.type,
                            "spec": server_spec
                        }
                    )
                    
                    if server_response.get("status") == "error":
                        error_msg = server_response.get("message", "Unknown error from iran node")
                        logger.error(f"Failed to restore tunnel {tunnel.id} on iran node {iran_node.id}: {error_msg}")
                        failed_count += 1
                        continue
                    
                    if not foreign_node.node_metadata.get("api_address"):
                        foreign_node.node_metadata["api_address"] = f"http://{foreign_node.node_metadata.get('ip_address', foreign_node.fingerprint)}:{foreign_node.node_metadata.get('api_port', 8888)}"
                        await db.commit()
                    
                    logger.info(f"Restoring tunnel {tunnel.id}: applying client config to foreign node {foreign_node.id}")
                    client_response = await client.send_to_node(
                        node_id=foreign_node.id,
                        endpoint="/api/agent/tunnels/apply",
                        data={
                            "tunnel_id": tunnel.id,
                            "core": tunnel.core,
                            "type": tunnel.type,
                            "spec": client_spec
                        }
                    )
                    
                    if client_response.get("status") == "error":
                        error_msg = client_response.get("message", "Unknown error from foreign node")
                        logger.error(f"Failed to restore tunnel {tunnel.id} on foreign node {foreign_node.id}: {error_msg}")
                        failed_count += 1
                    else:
                        logger.info(f"Successfully restored tunnel {tunnel.id} on both nodes")
                        restored_count += 1
                        
                except Exception as e:
                    logger.error(f"Failed to restore tunnel {tunnel.id}: {e}", exc_info=True)
                    failed_count += 1
            
            for tunnel in gost_tunnels:
                try:
                    logger.info(f"Restoring GOST tunnel {tunnel.id}: node_id={tunnel.node_id}, spec={tunnel.spec}")
                    
                    forward_to = tunnel.spec.get("forward_to") if tunnel.spec else None
                    listen_port = tunnel.spec.get("listen_port") or tunnel.spec.get("remote_port")
                    
                    if forward_to:
                        if ":" in forward_to:
                            foreign_ip, remote_port = forward_to.rsplit(":", 1)
                        else:
                            foreign_ip = forward_to
                            remote_port = tunnel.spec.get("remote_port", 8080)
                    else:
                        remote_ip = tunnel.spec.get("remote_ip", "127.0.0.1")
                        remote_port = tunnel.spec.get("remote_port", 8080)
                        foreign_ip = remote_ip
                    
                    if not listen_port:
                        logger.warning(f"GOST tunnel {tunnel.id}: Missing listen_port, checking if panel-side forwarding...")
                        panel_port = tunnel.spec.get("remote_port")
                        if panel_port:
                            listen_port = panel_port
                        else:
                            logger.warning(f"GOST tunnel {tunnel.id}: No listen_port found, skipping node-side restore (may be panel-side)")
                            continue
                    
                    if not tunnel.node_id:
                        logger.info(f"GOST tunnel {tunnel.id}: No node_id, this is a panel-side forwarding tunnel (handled by _restore_forwards)")
                        continue
                    
                    iran_node = None
                    if tunnel.node_id:
                        result = await db.execute(select(Node).where(Node.id == tunnel.node_id))
                        iran_node = result.scalar_one_or_none()
                        if iran_node and iran_node.node_metadata.get("role") != "iran":
                            logger.warning(f"GOST tunnel {tunnel.id}: node_id points to non-iran node, skipping")
                            continue
                    
                    if not iran_node:
                        result = await db.execute(select(Node))
                        all_nodes = result.scalars().all()
                        iran_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "iran"]
                        if iran_nodes:
                            iran_node = iran_nodes[0]
                    
                    if not iran_node:
                        logger.warning(f"GOST tunnel {tunnel.id}: No iran node found, skipping sync (node will restore itself)")
                        skipped_count += 1
                        continue
                    
                    if not foreign_ip or foreign_ip in ["127.0.0.1", "localhost"]:
                        result = await db.execute(select(Node))
                        all_nodes = result.scalars().all()
                        foreign_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"]
                        if foreign_nodes:
                            foreign_node = foreign_nodes[0]
                            foreign_ip = foreign_node.node_metadata.get("ip_address")
                    
                    if not foreign_ip:
                        logger.warning(f"GOST tunnel {tunnel.id}: Cannot determine foreign IP, skipping restore")
                        continue
                    
                    use_ipv6 = tunnel.spec.get("use_ipv6", False)
                    
                    gost_spec = {
                        "listen_port": int(listen_port),
                        "forward_to": f"{foreign_ip}:{remote_port}",
                        "type": tunnel.type,
                        "use_ipv6": use_ipv6
                    }
                    
                    client = NodeClient()
                    if not iran_node.node_metadata.get("api_address"):
                        iran_node.node_metadata["api_address"] = f"http://{iran_node.node_metadata.get('ip_address', iran_node.fingerprint)}:{iran_node.node_metadata.get('api_port', 8888)}"
                        await db.commit()
                    
                    logger.info(f"Restoring GOST tunnel {tunnel.id}: applying to iran node {iran_node.id}, spec={gost_spec}")
                    response = await client.send_to_node(
                        node_id=iran_node.id,
                        endpoint="/api/agent/tunnels/apply",
                        data={
                            "tunnel_id": tunnel.id,
                            "core": "gost",
                            "type": tunnel.type,
                            "spec": gost_spec
                        }
                    )
                    
                    if response.get("status") != "success":
                        error_msg = response.get("message", "Unknown error from iran node")
                        logger.error(f"Failed to restore GOST tunnel {tunnel.id} on iran node {iran_node.id}: {error_msg}")
                        failed_count += 1
                    else:
                        logger.info(f"Successfully restored GOST tunnel {tunnel.id} on iran node {iran_node.id}")
                        restored_count += 1
                        
                except Exception as e:
                    logger.error(f"Failed to restore GOST tunnel {tunnel.id}: {e}", exc_info=True)
                    failed_count += 1
            
            logger.info(f"Tunnel sync completed: {restored_count} synced, {failed_count} failed, {skipped_count} skipped out of {len(reverse_tunnels) + len(gost_tunnels)} total")
            logger.info("Note: Nodes restore their own tunnels on startup, so tunnels work even if panel is down")
                    
    except Exception as e:
        logger.error(f"Error restoring node tunnels: {e}", exc_info=True)


async def _load_and_start_frp_comm():
    """Load FRP communication settings and start server if enabled"""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Settings).where(Settings.key == "frp"))
            setting = result.scalar_one_or_none()
            if setting and setting.value and setting.value.get("enabled"):
                port = setting.value.get("port", 7000)
                token = setting.value.get("token")
                success = frp_comm_manager.start(port, token)
                if success:
                    logger.info(f"FRP communication server started on port {port}")
                else:
                    logger.warning(f"FRP communication server failed to start (binary may not be available)")
            else:
                logger.info("FRP communication is disabled")
    except Exception as e:
        logger.error(f"Error loading FRP communication settings: {e}", exc_info=True)


async def _load_and_start_telegram_bot():
    """Load Telegram bot settings and start bot if enabled"""
    try:
        await telegram_bot.start()
    except Exception as e:
        logger.error(f"Error starting Telegram bot: {e}", exc_info=True)


async def _load_and_start_tunnel_reapply():
    """Load tunnel reapply settings and start task if enabled"""
    try:
        from app.tunnel_reapply_manager import tunnel_reapply_manager
        await tunnel_reapply_manager.start()
    except Exception as e:
        logger.error(f"Error starting tunnel reapply manager: {e}", exc_info=True)


async def _auto_reset_scheduler(app: FastAPI):
    """Background task to auto-reset cores based on timer configuration"""
    from datetime import datetime, timedelta
    from app.routers.core_health import _reset_core
    
    while True:
        try:
            await asyncio.sleep(60)
            
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(CoreResetConfig).where(CoreResetConfig.enabled == True))
                configs = result.scalars().all()
                
                now = datetime.utcnow()
                
                for config in configs:
                    if not config.next_reset:
                        continue
                    
                    if now >= config.next_reset:
                        try:
                            logger.info(f"Auto-resetting {config.core} core (interval: {config.interval_minutes} minutes)")
                            
                            config.last_reset = now
                            config.next_reset = now + timedelta(minutes=config.interval_minutes)
                            await db.commit()
                            await db.refresh(config)  # Ensure config is refreshed after commit
                            
                            await _reset_core(config.core, app, db)
                            
                            logger.info(f"Auto-reset completed for {config.core}, next reset at {config.next_reset}")
                        except Exception as e:
                            logger.error(f"Error in auto-reset for {config.core}: {e}", exc_info=True)
                            await db.rollback()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in auto-reset scheduler: {e}", exc_info=True)
            await asyncio.sleep(60)


app = FastAPI(
    title="Smite Panel",
    description="Tunneling Control Panel",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(panel.router, prefix="/api/panel", tags=["panel"])
app.include_router(nodes.router, prefix="/api/nodes", tags=["nodes"])
app.include_router(tunnels.router, prefix="/api/tunnels", tags=["tunnels"])
app.include_router(status.router, prefix="/api/status", tags=["status"])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
app.include_router(core_health.router, prefix="/api/core-health", tags=["core-health"])
app.include_router(backup.router)
app.include_router(settings_router.router)

static_dir = os.path.join(os.path.dirname(__file__), "static")
static_path = Path(static_dir)

if static_path.exists() and (static_path / "index.html").exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static-assets")
    
    from fastapi.responses import FileResponse
    
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve frontend for all non-API routes"""
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("redoc") or full_path.startswith("openapi.json"):
            raise HTTPException(status_code=404)
        
        file_path = static_path / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        
        index_path = static_path / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        raise HTTPException(status_code=404)

@app.get("/")
async def root():
    """Root redirect"""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    index_path = Path(static_dir) / "index.html"
    if index_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(index_path)
    return {"message": "Smite Panel API", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    
    if settings.https_enabled:
        import ssl
        cert_path = Path(settings.https_cert_path).resolve()
        key_path = Path(settings.https_key_path).resolve()
        
        if not cert_path.exists() or not key_path.exists():
            logger.warning(f"HTTPS enabled but certificate files not found. Using HTTP.")
            uvicorn.run(app, host=settings.panel_host, port=settings.panel_port)
        else:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(str(cert_path), str(key_path))
            uvicorn.run(
                app,
                host=settings.panel_host,
                port=settings.panel_port,
                ssl_keyfile=str(key_path),
                ssl_certfile=str(cert_path)
            )
    else:
        uvicorn.run(app, host=settings.panel_host, port=settings.panel_port)
