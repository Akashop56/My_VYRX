from core.llm_handler import complete

try:
    print(complete("Hello RONIN", []))
except Exception as e:
    print(f"🔥 ASLI ERROR YEH HAI: {e}")

