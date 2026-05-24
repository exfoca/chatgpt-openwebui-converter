# ChatGPT to Open WebUI Converter

A simple and robust Python script that converts ChatGPT conversation exports into formats compatible with **Open WebUI** and other tools.

## What it does

- Converts `conversations.json` (and split files like `conversations-000.json`) from ChatGPT export.
- Generates:
  1. **Open WebUI** compatible JSON (ready for import).
  2. Clean TXT file optimized for RAG, local search, and document ingestion.
  3. NDJSON normalized format (great for pipelines).

## Requirements

- Python 3.8+
- No external dependencies

## Usage

### Basic usage

```bash
python chatgpt_openwebui_converter.py /path/to/export
```

### Options

| Argument              | Description                                      | Default                     |
|-----------------------|--------------------------------------------------|-----------------------------|
| `input_dir`           | Directory containing the export files            | Required                    |
| `--output-dir`        | Output directory                                 | `<input_dir>/output`        |
| `--model`             | Model name for Open WebUI                        | `openai/chatgpt-5`          |
| `--user-id`           | User ID for Open WebUI                           | Auto-generated              |
| `--only`              | Generate only specific format                    | `all`                       |

### Examples

**Generate all possible outputs:**
```bash
python chatgpt_openwebui_converter.py ./my_export --only all
```

**Generate only Open WebUI JSON:**
```bash
python chatgpt_openwebui_converter.py ./my_export --only openwebui
```

**Use a custom model:**
```bash
python chatgpt_openwebui_converter.py ./my_export --model "llama3.1:70b"
```

**Generate only TXT (clean version):**
```bash
python chatgpt_openwebui_converter.py ./my_export --only txt
```

**Custom output directory:**
```bash
python chatgpt_openwebui_converter.py ./my_export --output-dir ./converted_chats
```

## Output Files

Located in the `output/` folder (or your custom directory):

- `converted-for-open-webui.json` → Import directly into Open WebUI
- `chatgpt-conversations-clean.txt` → Clean text for RAG/documents
- `chatgpt-conversations-normalized.ndjson` → Structured line-by-line format
- `conversion-report.json` → Summary of the conversion process

## Notes

- The script preserves conversation structure, timestamps, and cleans text while maintaining code blocks and technical content.
- Duplicate conversations are automatically skipped.
- Works with both single and split export files.

---

Made with ❤️ for the community!
