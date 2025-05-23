import streamlit as st
import os
from dotenv import load_dotenv
from uuid import uuid4
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_mistralai import MistralAIEmbeddings
from langchain_mistralai import ChatMistralAI
from langchain_astradb import AstraDBVectorStore
from langchain.chains import create_retrieval_chain, create_history_aware_retriever
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain.schema import Document

load_dotenv()

# --- Configurations ---
ASTRA_DB_API_ENDPOINT = os.getenv("ASTRA_DB_API_ENDPOINT")
ASTRA_DB_APPLICATION_TOKEN = os.getenv("ASTRA_DB_APPLICATION_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

# --- Streamlit Page Setup ---
st.set_page_config(page_title="PDF QnA App", layout="wide")
st.markdown("""
    <style>
    .main {background-color: #f8f9fa;}
    .stButton>button {transition: all 0.3s ease;}
    .stButton>button:hover {background-color: #6c757d; color: white;}
    </style>
""", unsafe_allow_html=True)

# --- Sidebar PDF Upload ---
st.sidebar.title("📄 Upload your PDF")
pdf_file = st.sidebar.file_uploader("Choose a PDF", type=["pdf"])

if 'vector_ready' not in st.session_state:
    st.session_state.vector_ready = False

if st.sidebar.button("Upload & Process") and pdf_file:
    with st.spinner("Processing PDF..."):
        # Save the uploaded PDF
        with open("temp.pdf", "wb") as f:
            f.write(pdf_file.read())

        # Load, split, embed and store
        loader = PyPDFLoader("temp.pdf")
        pages = loader.load()

        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        texts = [chunk for page in pages for chunk in splitter.split_text(page.page_content)]
        docs = [Document(page_content=t) for t in texts]

        embeddings_model = MistralAIEmbeddings(model="mistral-embed")
        vector_store = AstraDBVectorStore(
            collection_name="pdf_store",
            embedding=embeddings_model,
            api_endpoint=ASTRA_DB_API_ENDPOINT,
            token=ASTRA_DB_APPLICATION_TOKEN,
        )

        uuids = [str(uuid4()) for _ in range(len(docs))]
        vector_store.add_documents(documents=docs, ids=uuids)
        st.session_state.vector_ready = True
        st.success("PDF Processed and Stored!")

# --- Chat Section ---
st.title("📚 Ask Questions About Your PDF")
if st.session_state.vector_ready:
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []

    user_question = st.text_input("Your Question:", key="input")
    if st.button("Ask") and user_question:
        with st.spinner("Generating answer..."):
            model = ChatMistralAI(
                model="mistral-large-latest",
                temperature=0.7,
                max_retries=2,)
            embeddings_model = embeddings = MistralAIEmbeddings(model="mistral-embed")
            vector_store = AstraDBVectorStore(
                collection_name="pdf_store",
                embedding=embeddings_model,
                api_endpoint=ASTRA_DB_API_ENDPOINT,
                token=ASTRA_DB_APPLICATION_TOKEN,
            )

            retriever = vector_store.as_retriever(search_type="mmr", search_kwargs={"k": 4})

        
            contextualize_q_system_prompt = """Given a chat history and the latest user question \
            which might reference context in the chat history, formulate a standalone question \
            which can be understood without the chat history. Do NOT answer the question, \
            just reformulate it if needed and otherwise return it as is."""
            contextualize_q_prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", contextualize_q_system_prompt),
                    MessagesPlaceholder("chat_history"),
                    ("human", "{input}"),
                ]
            )
            history_aware_retriever = create_history_aware_retriever(
                model, retriever, contextualize_q_prompt
            )


            ### Answer question ###
            qa_system_prompt = """You are an assistant for question-answering tasks. \
            Use the following pieces of retrieved context to answer the question. \
            If you don't know the answer, just say that you don't know. \
            Use three sentences maximum and keep the answer concise.\

            {context}"""
            qa_prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", qa_system_prompt),
                    MessagesPlaceholder("chat_history"),
                    ("human", "{input}"),
                ]
            )
            question_answer_chain = create_stuff_documents_chain(model, qa_prompt)

            rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)


            ### Statefully manage chat history ###
            store = {}


            def get_session_history(session_id: str) -> BaseChatMessageHistory:
                if session_id not in store:
                    store[session_id] = ChatMessageHistory()
                return store[session_id]


            conversational_rag_chain = RunnableWithMessageHistory(
                rag_chain,
                get_session_history,
                input_messages_key="input",
                history_messages_key="chat_history",
                output_messages_key="answer",
            )


            response = conversational_rag_chain.invoke({"input": user_question}, config={"configurable": {"session_id": "abc123"}})
            st.session_state.chat_history.append((user_question, response["answer"]))

    for q, a in st.session_state.chat_history:
        st.markdown(f"**You:** {q}")
        st.markdown(f"**Bot:** {a}")
else:
    st.info("Please upload and process a PDF to start chatting.")
