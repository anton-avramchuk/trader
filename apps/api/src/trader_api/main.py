from fastapi import FastAPI

app = FastAPI(title="Trader API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
