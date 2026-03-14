# 影刀对接 HTTP 示例

以下命令均在 PowerShell 执行。

## 1) 入队（多人 + 图片 + 表格）

```powershell
python -c "import requests, json; payload={'target_contacts':['GeyonGpan','测试号2'],'review_text':'这是自动发送的评价文本','image_paths':['D:/1-品创/langchain/data/generated_images/a.png'],'file_paths':['D:/1-品创/langchain/data/outbox_files/report.xlsx'],'max_retry':4}; r=requests.post('http://127.0.0.1:8000/outbox/enqueue',json=payload); print(r.status_code); print(json.dumps(r.json(),ensure_ascii=False,indent=2))"
```

## 2) 影刀拉取任务

```powershell
python -c "import requests, json; r=requests.get('http://127.0.0.1:8000/outbox/next',params={'worker_id':'yingdao-1'}); print(json.dumps(r.json(),ensure_ascii=False,indent=2))"
```

## 3) 成功回写

```powershell
python -c "import requests, json; payload={'task_id':'task_xxx','worker_id':'yingdao-1','note':'all sent'}; r=requests.post('http://127.0.0.1:8000/outbox/ack-success',json=payload); print(json.dumps(r.json(),ensure_ascii=False,indent=2))"
```

## 4) 失败回写（触发重试/死信）

```powershell
python -c "import requests, json; payload={'task_id':'task_xxx','worker_id':'yingdao-1','step':'SendFilesLoop','error_message':'文件发送失败','screenshot_path':'D:/xx/fail.png'}; r=requests.post('http://127.0.0.1:8000/outbox/ack-failure',json=payload); print(json.dumps(r.json(),ensure_ascii=False,indent=2))"
```

## 5) 心跳上报

```powershell
python -c "import requests; payload={'worker_id':'yingdao-1','status':'running','current_task_id':'task_xxx','meta':{'scene':'prod'}}; print(requests.post('http://127.0.0.1:8000/outbox/heartbeat',json=payload).text)"
```

## 6) 查看任务状态

```powershell
python -c "import requests, json; r=requests.get('http://127.0.0.1:8000/outbox/tasks',params={'limit':100}); print(json.dumps(r.json(),ensure_ascii=False,indent=2))"
```
