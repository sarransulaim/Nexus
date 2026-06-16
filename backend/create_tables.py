from database.core import engine
from database.models import Base

print("Creating all tables...")
Base.metadata.create_all(bind=engine)
print("✅ All tables created.")