# Redesign: export_files e import_files (on-demand, sem cópia para host)

## Resumo

- **export_files**: deixa de copiar para o host. Registra arquivos como "liberados" e retorna `[{session_id, path}]`. Download via endpoint HTTP + `docker exec`.
- **import_files**: aceita `{session_id, path}` (de outra sessão da mesma thread) ou `{source: host_path}` (arquivo da máquina do usuário).
- **Economia**: sem cópia em disco para transferências cross-session; download sob demanda.

---

## 1. Modelos de dados

### 1.1 ExportFileResult (novo contrato)

```python
@dataclass
class ExportFileResult:
    session_id: str
    path: str          # path dentro do container (ex: /workspace/report.pdf)
    success: bool
    size: int = 0
    error: str = ""
```

- Remove `source` e `destination` (host). Mantém `session_id` e `path` (container).
- `path` sempre absoluto dentro do container (ex: `/workspace/report.pdf`).

### 1.2 ImportFileResult

- Mantém `source`, `destination`, `success`, `size`, `error`.
- `source` pode ser:
  - path no host (string) — fluxo atual
  - referência cross-session: `session_id:path` ou objeto `{session_id, path}`

### 1.3 Entrada de import_files

Cada item em `files`:

```python
# Opção A: arquivo do host (comportamento atual)
{"source": "/path/on/host/data.csv", "destination": "data.csv"}

# Opção B: arquivo de outra sessão (mesma thread)
{"session_id": "abc123", "path": "/workspace/output.csv", "destination": "output.csv"}
```

- Se `session_id` presente: ignora `source`, usa `path` do container da sessão origem.
- Se `session_id` ausente ou null: usa `source` como path no host.

---

## 2. SandboxManager

### 2.1 Registro de arquivos exportados

```python
# Em SandboxManager.__init__
self._exported_files: dict[str, dict[str, set[str]]] = {}  # thread_id -> session_id -> set(paths)
```

- Chave: `thread_id`. Se sessão sem thread (MCP), usar `session_id` como "thread" fictício.
- Valor: `session_id -> set(paths)` (paths absolutos no container, ex: `/workspace/x.csv`).

### 2.2 export_files (novo)

**Assinatura:**
```python
def export_files(
    self,
    session_id: str,
    files: list[dict[str, str]],  # [{"source": "x.csv", "destination": "x.csv"}]
) -> ExportResult:
```

**Comportamento:**
1. Para cada `source` em `files`:
   - Resolver path no container (relativo → `/workspace/...`).
   - Verificar se existe no container (`docker exec ... test -e ...` ou `ls`).
   - Se existir: adicionar ao `_exported_files[thread_id][session_id]`.
   - Não copiar para o host.
2. Retornar `ExportResult` com `ExportFileResult(session_id, container_path, success, size, error)`.
3. `destination` no input pode ser ignorado ou usado só como hint para o nome no download (opcional).

**Validação de path:**
- Garantir que path resolvido está dentro de `/workspace/` (evitar path traversal).
- Exemplo: `path = /workspace/../etc/passwd` → rejeitar.

### 2.3 Método para validar download

```python
def is_file_exported(self, thread_id: str, session_id: str, path: str) -> bool:
    """Verifica se (session_id, path) está liberado para download."""
    paths = self._exported_files.get(thread_id, {}).get(session_id, set())
    # Normalizar path para comparação
    norm = str(Path(path).resolve())
    return any(
        norm == p or norm.startswith(p.rstrip("/") + "/")
        for p in paths
    )
```

### 2.4 Método para streamar arquivo

```python
def stream_exported_file(
    self,
    thread_id: str,
    session_id: str,
    path: str,
) -> Generator[bytes, None, None]:
    """Stream bytes do arquivo no container. Levanta ValueError se não liberado."""
    if not self.is_file_exported(thread_id, session_id, path):
        raise ValueError(f"File not exported: {session_id}:{path}")
    # ... docker exec tar cf - ...
```

### 2.5 import_files (atualizado)

Para cada entrada em `files`:

```python
if "session_id" in entry and entry["session_id"]:
    # Cross-session: copiar do container origem para container destino
    src_session = entry["session_id"]
    src_path = entry.get("path", "")
    dst = entry.get("destination", Path(src_path).name)
    # Validar: src_session pertence à mesma thread
    # Validar: (src_session, src_path) está em _exported_files
    # docker cp: container_src:path -> tar -> docker exec container_dst tar xf -
    ...
else:
    # Host: fluxo atual (source = path no host)
    src = entry.get("source", "")
    ...
```

**Implementação cross-session:**
- Usar `docker cp` ou pipe: `docker exec src tar cf - path | docker exec -i dst tar xf - -C /workspace/`.
- Ou: `docker cp container_src:/path - | docker cp - container_dst:/workspace/` (docker cp não suporta stdin assim).
- Melhor: `docker exec src tar cf - -C /workspace path | docker exec -i dst tar xf - -C /workspace`.

### 2.6 Limpeza

- Em `stop_session`: remover `_exported_files[thread_id][session_id]` e `_exported_files[thread_id]` se vazio.
- Em `cleanup_thread_sessions`: remover `_exported_files[thread_id]`.
- MCP (sem thread): usar `session_id` como chave de thread fictícia; limpar em `stop_session`.

---

## 3. Endpoint HTTP de download

### 3.1 Rota

```
GET /threads/{thread_id}/files/download?session_id={sid}&path={path}
```

- `path`: path no container, URL-encoded (ex: `/workspace/report.pdf` → `%2Fworkspace%2Freport.pdf`).

### 3.2 Comportamento

1. Validar `thread_id` (existe na API/checkpointer).
2. Chamar `manager.is_file_exported(thread_id, session_id, path)`.
3. Se não liberado: 403 Forbidden.
4. Se liberado: `manager.stream_exported_file(...)` → `StreamingResponse` com `Content-Disposition: attachment; filename="..."`.

### 3.3 Onde adicionar

- Em `http_app.py`: registrar a rota no `app` do FastAPI.
- O manager é obtido via `_get_manager()` (já existe).
- `thread_id` vem da URL; a API LangGraph/Aegra pode não expor threads diretamente — verificar se há middleware que injeta `thread_id` no config. Se a rota for custom, o `thread_id` vem da URL.

### 3.4 MCP (sem thread)

- Para MCP, não há `thread_id` no sentido da API.
- Opção: `GET /files/download?session_id={sid}&path={path}` (sem thread).
- Usar `session_id` como chave em `_exported_files` quando `thread_id` for None.

---

## 4. Tools (LangChain)

### 4.1 export_files

**Input:**
```python
session_id: str
files: list[dict[str, str]]  # [{"source": "report.pdf", "destination": "report.pdf"}]
```

**Output (JSON):**
```json
{
  "success": true,
  "files": [
    {
      "session_id": "abc123",
      "path": "/workspace/report.pdf",
      "success": true,
      "size": 12345,
      "error": ""
    }
  ]
}
```

**Descrição da tool:** Atualizar para explicar que os arquivos ficam disponíveis para download via API e para import em outras sessões da mesma conversa. O usuário pode baixar em `GET /threads/{thread_id}/files/download?session_id=...&path=...`.

### 4.2 import_files

**Input:**
```python
session_id: str  # sessão DESTINO
files: list[dict]  # cada um: {"source": "..."} OU {"session_id": "...", "path": "...", "destination": "..."}
```

**Descrição:** Explicar as duas formas: host path ou referência cross-session.

---

## 5. MCP

### 5.1 export_files

- Mesmo contrato: retorna `{session_id, path}`.
- Sem `output_dir` (não há mais cópia para host).
- Para o cliente MCP baixar: precisa de um endpoint HTTP. Se o MCP rodar no mesmo host que a API, o cliente pode usar a URL base + `/files/download?session_id=...&path=...` (sem thread — ver 3.4).

### 5.2 import_files

- Manter suporte a `file_content` + `file_name` (conteúdo inline).
- Manter `source` para path no host (servidor MCP).
- Adicionar `session_id` + `path` para cross-session.

### 5.3 Download no MCP

- O cliente MCP (ex: Cursor) pode não ter como chamar HTTP arbitrariamente.
- Alternativa: tool `download_file(session_id, path)` que retorna o conteúdo em base64 (para arquivos pequenos). Para grandes, o cliente precisaria de um endpoint HTTP configurável.

---

## 6. Frontend (Streamlit)

### 6.1 Parsing do resultado de export_files

- `file_results` agora tem `session_id` e `path` em vez de `destination`.
- Para cada arquivo exportado com sucesso: exibir botão "Baixar" que chama o endpoint de download.

### 6.2 Obter bytes para download

- Antes: `read_exported_file(dest)` (path no host).
- Agora: `httpx.get(f"{base_url}/threads/{thread_id}/files/download?session_id={sid}&path={path}")` e usar `response.content` no `st.download_button`.

### 6.3 check_exported_file

- Não faz mais sentido verificar path no host.
- Substituir por: verificar se `session_id` e `path` existem (o resultado da tool já indica sucesso). O botão de download sempre aparece para exports bem-sucedidos; se a sessão foi encerrada, o request retornará 403.

### 6.4 api_client

- Adicionar método:
```python
def download_exported_file(
    self,
    thread_id: str,
    session_id: str,
    path: str,
) -> bytes:
    r = self._client.get(
        f"/threads/{thread_id}/files/download",
        params={"session_id": session_id, "path": path},
    )
    r.raise_for_status()
    return r.content
```

---

## 7. CLI

### 7.1 Comportamento

- O CLI usa o manager diretamente (sem HTTP).
- `export_files` não grava mais no host.
- Para o usuário baixar um arquivo no CLI:
  - **Opção A:** Assumir que a API está rodando e fazer `httpx.get(...)` para o endpoint de download, salvando em disco.
  - **Opção B:** Adicionar flag `--copy-to-host` em `export_files` que mantém o comportamento antigo (copiar para STORAGE_DIR) — mas você disse que não precisa manter nada antigo.
- **Recomendação:** O CLI pode exibir os `{session_id, path}` e informar: "Para baixar, use a UI ou a API: GET /threads/{thread_id}/files/download?session_id=...&path=...". Se o usuário estiver no modo interativo com API rodando, o CLI poderia ter um comando `download` que chama a API.

### 7.2 Formatação de output

- Ao exibir resultado de `export_files`, mostrar `session_id` e `path` em vez de `destination`.

---

## 8. Prompts (SYSTEM_PROMPT)

### 8.1 export_files

- Remover menções a `STORAGE_DIR`, `destination` no host.
- Explicar: "export_files registra arquivos para download e para uso em import_files de outras sessões. Retorna session_id e path. O usuário pode baixar via a interface; para cross-session, use import_files com session_id e path."

### 8.2 import_files

- Documentar: "Para arquivo do host: use source (path no host). Para arquivo de outra sessão: use session_id e path (retornados por export_files)."

### 8.3 Cross-session transfer

- Atualizar: "Para transferir de sessão A para B: 1) export_files em A; 2) import_files em B com session_id=A e path=…"

---

## 9. Storage e uploads

### 9.1 STORAGE_DIR

- **Uploads:** continua em `STORAGE_DIR/<thread_id>/uploads/` (frontend salva arquivos enviados pelo usuário).
- **Exports:** não usa mais `STORAGE_DIR` para exports.
- **Limpeza:** `cleanup_thread_sessions` continua removendo `STORAGE_DIR/<thread_id>/` (uploads + qualquer subdir antigo). Não haverá mais `session_id` em subdirs de export.

### 9.2 stop_session

- Remover a limpeza de `STORAGE_DIR/.../session_id` (não há mais exports em disco).
- Manter `_exported_files` removendo a sessão do registro.

---

## 10. Testes

### 10.1 test_export_files.py

- **test_export_single_file:** Verificar que retorna `session_id` e `path`; que o arquivo está em `_exported_files`; que `stream_exported_file` retorna o conteúdo correto. Não verificar `Path(fr.destination).exists()`.
- **test_export_multiple_files:** Idem, validar registro em `_exported_files`.
- **test_stop_session_removes_exported_files:** Verificar que `_exported_files` é limpo; não verificar diretório em disco.
- **test_cleanup_thread_sessions_removes_thread_dir:** Manter para uploads; remover asserções sobre exports em disco.
- **test_tool_full_workflow:** Verificar JSON de retorno com `session_id` e `path`; não verificar arquivo em disco.
- **test_mcp_export:** Verificar retorno; não verificar `output_dir`.

### 10.2 test_manager.py (import)

- **test_import_from_exported_session:** Novo teste: export em sessão A, import em sessão B com `{session_id: A, path: "..."}`, verificar que o arquivo aparece em B.

### 10.3 test_import_files (se existir)

- Adicionar testes para o fluxo cross-session.

### 10.4 Teste do endpoint de download

- Novo arquivo ou seção: `test_download_endpoint`. Usar `TestClient` do FastAPI, criar sessão, export, chamar GET, verificar 200 e conteúdo.

---

## 11. Ordem de implementação sugerida

1. **Modelos** (`sandbox/models.py`): atualizar `ExportFileResult`, `ExportResult`.
2. **Manager** (`sandbox/manager.py`):
   - Adicionar `_exported_files`.
   - Reescrever `export_files`.
   - Adicionar `is_file_exported`, `stream_exported_file`.
   - Atualizar `import_files` para aceitar `session_id` + `path`.
   - Atualizar `stop_session` e `cleanup_thread_sessions`.
3. **HTTP** (`http_app.py`): adicionar rota de download.
4. **Tools** (`tools/export_files.py`, `tools/import_files.py`): atualizar contrato e descrições.
5. **MCP** (`mcp_server.py`): atualizar `export_files` e `import_files`.
6. **Frontend** (`utils.py`, `app.py`, `api_client.py`): usar endpoint de download.
7. **CLI** (`cli.py`): ajustar formatação de output.
8. **Prompts** (`agent/prompts.py`): atualizar documentação das tools.
9. **Testes**: ajustar e adicionar novos.

---

## 12. Validação de path (segurança)

```python
def _normalize_container_path(path: str) -> str:
    """Resolve path e garante que está dentro de /workspace."""
    p = Path(path)
    if not p.is_absolute():
        p = Path("/workspace") / p
    resolved = p.resolve()
    workspace = Path("/workspace").resolve()
    if not str(resolved).startswith(str(workspace)):
        raise ValueError(f"Path outside /workspace: {path}")
    return str(resolved)
```

Usar em `export_files` e ao validar `path` no download e no import cross-session.
