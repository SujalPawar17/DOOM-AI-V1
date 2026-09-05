import psutil

print('CPU Usage: {}%'.format(psutil.cpu_percent(interval=1)))
print('RAM Usage: {}%'.format(psutil.virtual_memory().percent))
print('Disk Usage: {}%'.format(psutil.disk_usage('/').percent))
