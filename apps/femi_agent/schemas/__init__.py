"""
Point d'entrée unique du package schemas — même pattern que
apps/femi_agent/agent/tools/__init__.py.

Ré-exporte tout ce que contenait l'ancien apps/femi_agent/schemas.py,
pour que les imports existants continuent de fonctionner SANS AUCUNE
MODIFICATION ailleurs dans le code :

    from apps.femi_agent.schemas import ParsedOperationSchema   # toujours valide
    from apps.femi_agent.schemas import ProcessResult            # toujours valide

Expose en plus les schémas du nouveau système (schemas/accounting.py) :

    from apps.femi_agent.schemas import AccountingExtractionResult
"""

from .ollama_gbnf_extraction import (
    PaymentMethodEnum,
    LigneVenteExtraite,
    LignePrestationExtraite,
    BaseOperationSchema,
    LLMExtractionSchema,
    ParsedOperationSchema,
    LLMExtractionSchemaCommerce,
    LLMExtractionSchemaService,
)
from .common import ProcessResult
from .accounting import (
    AccountingPaymentMethodEnum,
    AccountingConfidenceEnum,
    AccountingTransactionSchema,
    AccountingExtractionResult,
    AccountingTransactionLLMSchema,
    AccountingExtractionLLMResult,
)

from .router import (
    RouterAgent,
    RouterActionType,
    RouterIntent,
    RouterOutput,
    RouterProcessResult,
    
)

from .accounting_modify import (
    SearchCriteria,
    OperationCandidate,
    AccountingModifySearchResult,
    AccountingModifyResolutionResult,
    AccountingModifyProposeChangeResult,
    AccountingModifyResult,
)

from .tool_loop import (
    ToolCallSchema,
    ToolSelectionOutput,
    FinalAnswerOutput,
    ValidationOutput,
)

from .customer import (
    CustomerExtractionOutput,
    CustomerFirstStepOutput,
)

from .ocr import (
    OcrEnTeteSchema,
    OcrLigneArticleSchema,
    OcrTotauxSchema,
    OcrExtractionResult,
)

from .stt import (
    UncertainSegmentType,
    SttConfidence,
    UncertainSegmentSchema,
    SttPostProcessingResult,
)
__all__ = [
    # ollama_gbnf_extraction.py (ancien pipeline)
    "PaymentMethodEnum",
    "LigneVenteExtraite",
    "LignePrestationExtraite",
    "BaseOperationSchema",
    "LLMExtractionSchema",
    "ParsedOperationSchema",
    "LLMExtractionSchemaCommerce",
    "LLMExtractionSchemaService",
    # common.py
    "ProcessResult",
    # accounting.py (nouveau système Router/ACCOUNTING_PROMPT)
    "AccountingPaymentMethodEnum",
    "AccountingConfidenceEnum",
    "AccountingTransactionSchema",
    "AccountingExtractionResult",
    "AccountingTransactionLLMSchema",
    "AccountingExtractionLLMResult",
    
    
        # router.py (sortie structurée du ROUTER_PROMPT)
    "RouterAgent",
    "RouterActionType",
    "RouterIntent",
    "RouterOutput",
    "RouterProcessResult",
    
     # tool_loop.py (pattern boucle-avec-tools : FINANCIAL_ANALYST, sous-flux READ de CUSTOMER)
    "ToolCallSchema",
    "ToolSelectionOutput",
    "FinalAnswerOutput",
    "ValidationOutput",
    "CustomerExtractionOutput",
    "CustomerFirstStepOutput",
    
    
        # accounting_modify.py (sortie structurée d'ACCOUNTING_MODIFY_PROMPT)
    "SearchCriteria",
    "OperationCandidate",
    "AccountingModifySearchResult",
    "AccountingModifyResolutionResult",
    "AccountingModifyProposeChangeResult",
    "AccountingModifyResult",
    # ocr.py (sortie structurée d'OCR_PROMPT)
    "OcrEnTeteSchema",
    "OcrLigneArticleSchema",
    "OcrTotauxSchema",
    "OcrExtractionResult",
    # stt.py (sortie structurée de STT_PROMPT)
    "UncertainSegmentType",
    "SttConfidence",
    "UncertainSegmentSchema",
    "SttPostProcessingResult",
]
