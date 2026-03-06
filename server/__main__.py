"""Entry point: python -m threat_backend"""
import uvicorn

if __name__ == "__main__":
    print("=" * 58)
    print("  Biodefense Threat Intelligence Backend (Layer 3)")
    print("=" * 58)
    print("  Port       : 8100")
    print("  Docs       : http://localhost:8100/docs")
    print("  Health     : http://localhost:8100/health")
    print("  Threats    : http://localhost:8100/threats/{city}")
    print("  Report     : http://localhost:8100/threats/{city}/report")
    print("  Subscribe  : POST http://localhost:8100/subscribe")
    print("  Unsubscribe: POST http://localhost:8100/unsubscribe")
    print("  Cities     : http://localhost:8100/cities")
    print("=" * 58)
    print()

    uvicorn.run(
        "server.app:app",
        host="127.0.0.1",
        port=8100,
        log_level="info",
    )
