import os
import sys
sys.path.insert(0, r'D:\1-品创\langchain')
from outbox import list_tasks
from config import settings

db_path = settings.outbox.db_path

pending = list_tasks(db_path, limit=50, status="pending")
processing = list_tasks(db_path, limit=50, status="processing")
completed = list_tasks(db_path, limit=50, status="completed")

print('Pending:', len(pending))
print('Processing:', len(processing))
print('Completed:', len(completed))

for t in pending[:3]:
    print('Pending:', t.task_id, t.review_text[:50] if t.review_text else 'N/A')

for t in completed[-3:]:
    print('Completed:', t.task_id, t.status)
