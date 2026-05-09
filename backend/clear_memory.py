"""
clear_memory.py — Run this once to fix corrupted agent memory
Usage: python clear_memory.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.core import SessionLocal
from database.models import AgentMemory
import json

db = SessionLocal()
try:
    records = db.query(AgentMemory).all()
    print(f"Found {len(records)} agent memory records")
    
    for record in records:
        record.memory_json = json.dumps([])
        record.message_count = 0
        print(f"  Cleared: {record.agent_id}")
    
    db.commit()
    print("\n✅ All agent memories cleared. Fresh start.")
except Exception as e:
    print(f"❌ Error: {e}")
finally:
    db.close()