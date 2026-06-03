"""企业投标模板库。"""

from .loader import load_bid_templates, parse_bid_template_docx, save_bid_template
from .matcher import recommend_bid_templates

__all__ = ["load_bid_templates", "parse_bid_template_docx", "recommend_bid_templates", "save_bid_template"]
