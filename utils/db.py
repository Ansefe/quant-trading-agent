import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️ Advertencia: No se encontraron las credenciales de Supabase en las variables de entorno.")
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Error conectando a Supabase: {e}")
        return None

def insert_sentiment(data_list):
    client = get_supabase_client()
    if not client or not data_list: return
    try:
        response = client.table("sentiment_analysis").insert(data_list).execute()
        print(f"✅ Supabase: Insertados {len(data_list)} registros de sentimiento.")
    except Exception as e:
        print(f"❌ Error insertando en sentiment_analysis: {e}")

def insert_sr_levels(data_list):
    client = get_supabase_client()
    if not client or not data_list: return
    try:
        response = client.table("support_resistance_levels").insert(data_list).execute()
        print(f"✅ Supabase: Insertados {len(data_list)} muros (Soporte/Resistencia).")
    except Exception as e:
        print(f"❌ Error insertando en support_resistance_levels: {e}")

def insert_rsi_divergences(data_list):
    client = get_supabase_client()
    if not client or not data_list: return
    try:
        response = client.table("rsi_divergences").insert(data_list).execute()
        print(f"✅ Supabase: Insertadas {len(data_list)} divergencias RSI.")
    except Exception as e:
        print(f"❌ Error insertando en rsi_divergences: {e}")

def insert_fvgs(data_list):
    client = get_supabase_client()
    if not client or not data_list: return
    try:
        response = client.table("fair_value_gaps").insert(data_list).execute()
        print(f"✅ Supabase: Insertados {len(data_list)} FVGs.")
    except Exception as e:
        print(f"❌ Error insertando en fair_value_gaps: {e}")

def insert_trade_confluences(data_list):
    client = get_supabase_client()
    if not client or not data_list: return
    try:
        response = client.table("trade_confluences").insert(data_list).execute()
        print(f"✅ Supabase: Insertadas {len(data_list)} CONFLUENCIAS.")
    except Exception as e:
        print(f"❌ Error insertando en trade_confluences: {e}")
