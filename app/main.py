from fastapi import FastAPI

app = FastAPI(title="Prove It")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
