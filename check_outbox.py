import os, sys
sys.path.insert(0, r'D:\1-品创\langchain')
from outbox import get_pending_tasks, get_completed_tasks

pending = get_pending_tasks()
completed = get_completed_tasks()

print('Pending:', len(pending))
print('Completed:', len(completed))

for t in pending[:3]:
    tid = t.get('id', 'N/A')
    print('Pending task:', tid)

for t in completed[-3:]:
    tid = t.get('id', 'N/A')
    status = t.get('status', 'N/A')
    print('Completed:', tid, status)
