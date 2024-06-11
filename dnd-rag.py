from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain_core.runnables import RunnablePassthrough
from langchain_community.chat_models import ChatOllama
from langchain_core.output_parsers import StrOutputParser
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

import os
import streamlit as st

def pdf_loader():
    documents = []
    pdf_directory = r"C:\Users\ataka\OneDrive\Desktop\HomeREG\Dnd-RAG"
    
    for file in os.listdir(pdf_directory):
        if file.endswith(".pdf"):
            loader = PyPDFLoader(os.path.join(pdf_directory, file))
            documents.extend(loader.load())
   
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1024, chunk_overlap=100)
    chunked_docs = text_splitter.split_documents(documents)

    vectordb = Chroma.from_documents(
        documents=chunked_docs,
        embedding=OllamaEmbeddings(model="nomic-embed-text",show_progress=True),
        collection_name="local-rag",
    )
    return vectordb

def get_llm_response(form_input):
    llm = ChatOllama(model="mistral")

    QUERY_PROMPT = PromptTemplate(
        input_variables=["question"],
        template="""You are an AI language model assistant. Your task is to generative five different versions of the given user question to
        retrieve relevant documents from a vector database. By generating multiple perspectives on the user question, your goal is to help the user overcome some of the limitations
        of the distance-based similarity search. Provide these alternative questions separated by newlines. Original question: {question}"""
    )

    retriever  = MultiQueryRetriever.from_llm(
        pdf_loader().as_retriever(),
        llm,
        prompt=QUERY_PROMPT
    )

    template = """Answer the question based ONLY on the following context:
    {context}
    Question: {question}
    """

    prompt = ChatPromptTemplate.from_template(template)

    chain = (
        {"context": retriever, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )

    return chain.invoke(input=(form_input))

st.header("Dnd RAG Chatbot - Mistral")

form_input = st.text_input('Enter Query')
submit = st.button("Generate")

if submit:
    response = get_llm_response(form_input)
    st.write(response)
