from app.knowledge_base.vectorstore import build_or_load_vectorstore

if __name__ == "__main__":
    vs = build_or_load_vectorstore()

    print("VS in ingest_knowledge_base : ", vs)
    print("vs._dict : ", vs.docstore)

    document_count = len(vs.docstore._dict)

    print(
        f"Vectorstore ready. Document count: {document_count}"
    )