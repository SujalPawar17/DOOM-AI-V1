import psutil

def main():
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    print(f"CPU Usage: {cpu}%")
    print(f"RAM Usage: {mem.percent}% ({mem.used/1024**3:.2f} GB used of {mem.total/1024**3:.2f} GB)")
    print(f"Disk Usage: {disk.percent}% ({disk.used/1024**3:.2f} GB used of {disk.total/1024**3:.2f} GB)")

if __name__ == "__main__":
    main()
