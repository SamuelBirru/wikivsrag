Run the physics wiki creation pipeline for ArXiv LaTeX papers.

Arguments (from $ARGUMENTS):
  --output-dir <dir>     Where to write wiki markdown pages (default: wiki_output)
  --paper <arxiv_id>     Regenerate wiki for one paper only
  --dry-run              Preview what pages would be generated without calling Claude
  --ingest               Re-run ingestion before generating (required on first run)
  --ingest-only          Run ingestion but skip wiki generation

Steps to follow:

1. Parse $ARGUMENTS to extract --output-dir, --paper, --dry-run, --ingest, --ingest-only flags.

2. If --ingest or --ingest-only is present, or if rag_chunks.pkl does not exist, run ingestion first:
   - Check if source_index.json exists. If not, tell the user to run:
       python PhysicsScript.py --download-source
   - Run: python rag_system.py --ingest-sources
   - Report the chunk counts and type breakdown from the output.
   - If --ingest-only, stop here.

3. Run wiki generation:
   - Build the command: python wiki_generate.py --output-dir <output_dir>
   - Append --paper <id> if provided
   - Append --dry-run if provided
   - Run the command and report progress.

4. After generation completes, report:
   - Total wiki pages written
   - Output directory path
   - Quality check results (equations, figures, missing captions)
   - Tell the user they can browse _index.json or open any .md file in the output dir.

5. If any step fails:
   - Missing AWS credentials → remind user to check .env for AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
   - Missing rag_chunks.pkl → run --ingest first
   - API error on a page → note it was skipped, generation continues

Example invocations:
  /physics-wiki-create --ingest --output-dir my_wiki
  /physics-wiki-create --paper 2301.12345 --output-dir my_wiki
  /physics-wiki-create --dry-run
