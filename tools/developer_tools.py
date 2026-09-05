"""
DOOM V3.2 — Developer Arsenal Tools
1. ProjectScaffolderTool: Scaffolds complete, production-ready backend/frontend codebases
2. APITesterTool: Tests REST API endpoints, benchmarks latency, and inspects responses
"""

import os
import json
import time
import requests
from typing import Dict, Any, List, Optional
from tools.base import BaseTool, ToolResult
from database.postgres_db import postgres_manager


class ProjectScaffolderTool(BaseTool):
    name = "developer_scaffold_project"
    description = "Instantly scaffolds a complete, production-ready software project structure with AI-tailored code matching user requirements"
    permission_level = "standard"
    timeout = 30
    parameters = {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Name of the project folder (e.g. 'OrderService', 'HospitalApp')"
            },
            "template": {
                "type": "string",
                "enum": ["fastapi_postgres", "react_vite", "nextjs_fullstack", "flask_microservice"],
                "description": "Stack template to scaffold"
            },
            "description": {
                "type": "string",
                "description": "Detailed description of the app features, database models, and endpoints to generate (e.g. 'E-commerce API with stripe payments, cart, and product inventory with JWT auth')"
            },
            "target_dir": {
                "type": "string",
                "description": "Optional parent directory path (defaults to user Desktop or workspace)"
            }
        },
        "required": ["project_name", "template"]
    }

    def _execute_impl(self, project_name: str, template: str = "fastapi_postgres", description: Optional[str] = None, target_dir: Optional[str] = None, **kwargs) -> ToolResult:
        start_t = time.time()
        base_dir = target_dir or os.path.join(os.path.expanduser("~"), "Desktop")
        project_path = os.path.join(base_dir, project_name)
        os.makedirs(project_path, exist_ok=True)

        created_files = []
        custom_main_code = None

        # If user supplied a description, use Groq to generate customized domain code
        if description and description.strip():
            try:
                from models.groq_provider import GroqProvider
                groq = GroqProvider()
                prompt = (
                    f"Generate a single production-ready starter file for project '{project_name}' using stack '{template}'.\n"
                    f"User Requirements: {description}\n"
                    f"Rules:\n"
                    f"- Write complete, runnable code with realistic domain models, schemas, and REST endpoints.\n"
                    f"- Provide ONLY code inside standard markdown code block. No explanations."
                )
                custom_code = groq.generate(prompt)
                if "```" in custom_code:
                    lines = custom_code.split("```")[1].split("\n")
                    if lines[0].strip() in ["python", "py", "typescript", "tsx", "javascript", "js"]:
                        lines = lines[1:]
                    custom_main_code = "\n".join(lines).strip()
            except Exception as e:
                print(f"[DOOM SCAFFOLDER] Groq customization warning: {e}")

        if template == "fastapi_postgres":
            main_content = custom_main_code or (
                '''from fastapi import FastAPI, Depends, HTTPException\nfrom pydantic import BaseModel\nfrom typing import List, Optional\nimport uvicorn\n\napp = FastAPI(title="''' + project_name + '''", version="1.0.0")\n\nclass Item(BaseModel):\n    id: Optional[int] = None\n    name: str\n    description: Optional[str] = None\n    price: float\n\nitems_db = []\n\n@app.get("/")\ndef health_check():\n    return {"status": "ONLINE", "service": "''' + project_name + '''"}\n\n@app.get("/items", response_model=List[Item])\ndef list_items():\n    return items_db\n\n@app.post("/items", response_model=Item)\ndef create_item(item: Item):\n    item.id = len(items_db) + 1\n    items_db.append(item)\n    return item\n\nif __name__ == "__main__":\n    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)\n'''
            )
            files = {
                "main.py": main_content,
                "requirements.txt": "fastapi==0.110.0\nuvicorn[standard]==0.29.0\npydantic==2.6.4\npsycopg2-binary==2.9.9\nsqlalchemy==2.0.29\npython-dotenv==1.0.1\npytest==8.1.1\n",
                "Dockerfile": "FROM python:3.11-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nEXPOSE 8000\nCMD [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]\n",
                "docker-compose.yml": 'version: "3.8"\nservices:\n  api:\n    build: .\n    ports:\n      - "8000:8000"\n    environment:\n      - DATABASE_URL=postgresql://postgres:postgres@db:5432/' + project_name.lower() + '\n    depends_on:\n      - db\n  db:\n    image: postgres:15-alpine\n    environment:\n      - POSTGRES_USER=postgres\n      - POSTGRES_PASSWORD=postgres\n      - POSTGRES_DB=' + project_name.lower() + '\n    ports:\n      - "5433:5432"\n    volumes:\n      - pgdata:/var/lib/postgresql/data\nvolumes:\n  pgdata:\n',
                ".env.example": "DATABASE_URL=postgresql://postgres:postgres@localhost:5432/" + project_name.lower() + "\nSECRET_KEY=supersecretkey\nDEBUG=True\n",
                "README.md": f"# {project_name}\n\n**Requirements:** {description or 'Standard Microservice'}\n\nProduction FastAPI service generated autonomously by **DOOM V2** for Boss Sujal.\n\n## Quick Start\n```bash\npip install -r requirements.txt\npython main.py\n```\n"
            }

        elif template == "react_vite":
            # 2. React + Vite + TypeScript
            src_dir = os.path.join(project_path, "src")
            os.makedirs(src_dir, exist_ok=True)
            files = {
                "package.json": '{\n  "name": "' + project_name.lower() + '",\n  "private": true,\n  "version": "1.0.0",\n  "type": "module",\n  "scripts": {\n    "dev": "vite",\n    "build": "tsc && vite build",\n    "preview": "vite preview"\n  },\n  "dependencies": {\n    "react": "^18.2.0",\n    "react-dom": "^18.2.0",\n    "lucide-react": "^0.359.0"\n  },\n  "devDependencies": {\n    "@types/react": "^18.2.66",\n    "@types/react-dom": "^18.2.22",\n    "@vitejs/plugin-react": "^4.2.1",\n    "typescript": "^5.2.2",\n    "vite": "^5.1.6"\n  }\n}\n',
                "src/App.tsx": 'import React, { useState } from "react";\n\nexport default function App() {\n  const [count, setCount] = useState(0);\n  return (\n    <div style={{ padding: "2rem", fontFamily: "system-ui, sans-serif", background: "#0a0f1e", color: "#e0f2fe", minHeight: "100vh" }}>\n      <h1>' + project_name + '</h1>\n      <p>Scaffolded by DOOM V2</p>\n      <button onClick={() => setCount(c => c + 1)} style={{ padding: "0.5rem 1rem", background: "#00f0ff", color: "#000", border: "none", borderRadius: "6px", cursor: "pointer" }}>\n        Count: {count}\n      </button>\n    </div>\n  );\n}\n',
                "src/main.tsx": 'import React from "react";\nimport ReactDOM from "react-dom/client";\nimport App from "./App";\n\nReactDOM.createRoot(document.getElementById("root")!).render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>\n);\n',
                "index.html": '<!DOCTYPE html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n    <title>' + project_name + '</title>\n  </head>\n  <body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.tsx"></script>\n  </body>\n</html>\n',
                "vite.config.ts": 'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\nexport default defineConfig({\n  plugins: [react()],\n});\n',
                "tsconfig.json": '{\n  "compilerOptions": {\n    "target": "ES2020",\n    "useDefineForClassFields": true,\n    "lib": ["ES2020", "DOM", "DOM.Iterable"],\n    "module": "ESNext",\n    "skipLibCheck": true,\n    "moduleResolution": "bundler",\n    "allowImportingTsExtensions": true,\n    "resolveJsonModule": true,\n    "isolatedModules": true,\n    "noEmit": true,\n    "jsx": "react-jsx",\n    "strict": True,\n    "noUnusedLocals": true,\n    "noUnusedParameters": true,\n    "noFallthroughCasesInSwitch": true\n  },\n  "include": ["src"]\n}\n',
                "README.md": f"# {project_name}\n\nReact + Vite + TypeScript application generated by DOOM V2.\n\n```bash\nnpm install\nnpm run dev\n```\n"
            }

        elif template == "flask_microservice":
            # 3. Flask Microservice
            files = {
                "app.py": 'from flask import Flask, jsonify, request\n\napp = Flask(__name__)\n\n@app.route("/")\ndef index():\n    return jsonify({"service": "' + project_name + '", "status": "UP"})\n\n@app.route("/api/health")\ndef health():\n    return jsonify({"health": "100%", "uptime": "nominal"})\n\nif __name__ == "__main__":\n    app.run(host="0.0.0.0", port=5000, debug=True)\n',
                "requirements.txt": "flask==3.0.2\npytest==8.1.1\ngunicorn==21.2.0\npython-dotenv==1.0.1\n",
                "Dockerfile": "FROM python:3.11-alpine\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nEXPOSE 5000\nCMD [\"gunicorn\", \"--bind\", \"0.0.0.0:5000\", \"app:app\"]\n",
                "README.md": f"# {project_name}\n\nFlask microservice scaffolded by DOOM V2.\n\n```bash\npip install -r requirements.txt\npython app.py\n```\n"
            }

        else:
            # 4. Generic/Next.js Fullstack
            files = {
                "package.json": '{\n  "name": "' + project_name.lower() + '",\n  "version": "0.1.0",\n  "private": true,\n  "scripts": {\n    "dev": "next dev",\n    "build": "next build",\n    "start": "next start"\n  },\n  "dependencies": {\n    "next": "^14.1.0",\n    "react": "^18.2.0",\n    "react-dom": "^18.2.0"\n  }\n}\n',
                "README.md": f"# {project_name}\n\nNext.js 14 Fullstack App scaffolded by DOOM V2.\n"
            }

        # Write files
        for rel_path, content in files.items():
            full_file_path = os.path.join(project_path, rel_path)
            os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
            with open(full_file_path, "w", encoding="utf-8") as f:
                f.write(content)
            created_files.append(rel_path)

        # Log to PostgreSQL
        if postgres_manager.is_connected():
            postgres_manager.save_semantic_fact(
                key=f"scaffolded_project_{project_name.lower()}",
                value={"name": project_name, "template": template, "path": project_path, "files": len(created_files)},
                category="developer"
            )

        duration = (time.time() - start_t) * 1000
        return ToolResult(
            success=True,
            output=f"Successfully scaffolded '{project_name}' ({template}) at '{project_path}' with {len(created_files)} files.",
            action="scaffold_project",
            artifact={"project_name": project_name, "template": template, "path": project_path, "files": created_files, "count": len(created_files)},
            stdout=f"Scaffolded {project_name} with {len(created_files)} files",
            stderr="",
            duration_ms=duration,
            exit_code=0,
            target=project_path,
            data={"project_name": project_name, "path": project_path, "files": created_files}
        )


class APITesterTool(BaseTool):
    name = "developer_test_api"
    description = "Sends an HTTP request to any REST API endpoint, measures precise latency in milliseconds, and returns status code and response payload"
    permission_level = "safe"
    timeout = 15
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to test (e.g. 'https://api.github.com/users/sujal')"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "description": "HTTP Method"},
            "headers": {"type": "object", "description": "Optional HTTP headers dict"},
            "body": {"type": "object", "description": "Optional JSON payload body for POST/PUT"}
        },
        "required": ["url"]
    }

    def _execute_impl(self, url: str, method: str = "GET", headers: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None, **kwargs) -> ToolResult:
        start_t = time.time()
        method = method.upper().strip()
        headers = headers or {"User-Agent": "DOOM-V2-API-Tester/2.0"}

        try:
            if method == "POST":
                resp = requests.post(url, json=body, headers=headers, timeout=10)
            elif method == "PUT":
                resp = requests.put(url, json=body, headers=headers, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(url, headers=headers, timeout=10)
            elif method == "PATCH":
                resp = requests.patch(url, json=body, headers=headers, timeout=10)
            else:
                resp = requests.get(url, headers=headers, timeout=10)

            duration = (time.time() - start_t) * 1000
            latency_ms = round(duration, 2)

            try:
                json_data = resp.json()
            except Exception:
                json_data = resp.text[:1000]

            summary = (
                f"API Test Result for {method} {url}:\n"
                f"• Status Code: {resp.status_code} ({resp.reason})\n"
                f"• Latency: {latency_ms} ms\n"
                f"• Content-Type: {resp.headers.get('content-type', 'unknown')}\n"
                f"• Response Size: {len(resp.content)} bytes"
            )

            return ToolResult(
                success=resp.ok,
                output=summary,
                action="test_api",
                artifact={"url": url, "method": method, "status_code": resp.status_code, "latency_ms": latency_ms},
                stdout=summary,
                stderr=resp.text if not resp.ok else "",
                duration_ms=duration,
                exit_code=resp.status_code,
                target=url,
                data={
                    "status_code": resp.status_code,
                    "reason": resp.reason,
                    "latency_ms": latency_ms,
                    "headers": dict(resp.headers),
                    "response": json_data
                }
            )
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            latency_ms = round(duration, 2)
            return ToolResult(
                success=False,
                output=f"API request failed to {url}: {e}",
                error=str(e),
                action="test_api",
                artifact={"url": url, "method": method, "error": str(e)},
                stdout="",
                stderr=str(e),
                duration_ms=duration,
                exit_code=-1,
                target=url,
                data={"latency_ms": latency_ms, "error": str(e)}
            )
