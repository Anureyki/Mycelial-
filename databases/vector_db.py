import chromadb
from chromadb.utils import embedding_functions

client = chromadb.PersistentClient(path="./chroma_db")
embeddings = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

# Create collection for knowledge base
collection = client.get_or_create_collection(
    name="mycelial_knowledge",
    embedding_function=embeddings
)

# Add your documents
# collection.add(documents=[...], ids=[...])
