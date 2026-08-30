from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from apps.femi_agent.schemas import TransactionExtracted
from apps.femi_agent.prompts import ACCOUNTING_SYSTEM_PROMPT

def get_femi_agent(model_name: str = "mistral"):
    """Instancie la chaîne LangChain configurée avec sortie Pydantic forcée."""
    
    # Modèle Open Source local
    llm = ChatOllama(
        model=model_name,
        temperature=0.0
    )
    
    # Binding du schéma Pydantic
    structured_llm = llm.with_structured_output(TransactionExtracted)
    
    # Prompt Template
    prompt = ChatPromptTemplate.from_messages([
        ("system", ACCOUNTING_SYSTEM_PROMPT),
        ("human", "Analyse le texte suivant : \n\n{input_text}")
    ])
    
    return prompt | structured_llm

def analyze_accounting_text(text: str) -> TransactionExtracted:
    """Point d'entrée pour traiter un texte et retourner un objet Pydantic."""
    chain = get_femi_agent()
    return chain.invoke({"input_text": text})