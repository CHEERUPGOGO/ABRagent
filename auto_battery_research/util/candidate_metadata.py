"""候选展示元数据兼容：展示名与材料身份分离，角色和排行仍须同条来源支持。"""
import copy
import re

from .research_contract import normalized

ROLES = {"主推候选": "candidate", "备选候选": "candidate", "对照组": "control", "文献参照": "literature"}
ROLE_WORDS = {"主推候选": ("主推候选",), "备选候选": ("备选候选",),
              "对照组": ("对照组", "对照"), "文献参照": ("文献参照",)}
RANK = re.compile(r"(?<![A-Za-z0-9])(?:rank\s*[:：#]?\s*|#\s*)(\d+)(?![\d.])|第\s*(\d+)\s*名", re.I)


def rank_number(match):
    return int(match[1] or match[2])


def visible(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).replace("**", "").replace("`", "")


def occurs(token, text):
    needle = normalized(token)
    return bool(needle) and bool(re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", normalized(visible(text))))


def local_sources(name, quotes):
    """同一表格行或同一分句定位，防止从另一候选借角色、名称或排行。"""
    for quote in quotes:
        if not isinstance(quote, str):
            continue
        for line in visible(quote).splitlines():
            pieces = [line] if line.lstrip().startswith("|") else re.split(r"[。；;，,]|(?<!\d)\.(?!\d)", line)
            for part in pieces:
                if occurs(name, part):
                    yield part


def positive_role(text, role):
    for word in ROLE_WORDS[role]:
        for match in re.finditer(re.escape(word), text):
            prefix = text[max(0, match.start() - 14):match.start()]
            if not re.search(r"(?:不是|并非|而非|不作为|不能作为|不得作为|不作|非|未选为|不列为)[^，。；|]{0,6}$", prefix):
                return True
    return False


def rank_supported(rank, name, quotes):
    for source in local_sources(name, quotes):
        if any(rank_number(m) == rank for m in RANK.finditer(source)):
            return True
        # 排名表首列仅含整数时可采用；配方系数、性能数字不能冒充排行。
        if source.lstrip().startswith("|"):
            first = source.strip().strip("|").split("|", 1)[0].strip()
            if first.isdecimal() and int(first) == rank:
                return True
    return False


def _display_supported(display, name, role, rank, sources):
    shown_ranks = [rank_number(m) for m in RANK.finditer(display)]
    if shown_ranks and (rank is None or any(value != rank for value in shown_ranks)):
        raise ValueError("候选展示排行与rank字段不一致")
    label = RANK.sub("", display).strip(" \t:：-—·")
    if not label:
        return False
    # 自动生成的“材料名＋已核验角色”可用；其余简称须在同条来源中确实出现。
    if role and re.fullmatch(re.escape(normalized(name)) + r"\(?" + re.escape(normalized(role)) + r"\)?", normalized(label)):
        return True
    target = normalized(label).replace("（", "(").replace("）", ")")
    for source in sources:
        text = normalized(source)
        if re.search(r"(?<![a-z0-9])" + re.escape(target) + r"(?![a-z0-9])", text):
            if not shown_ranks or rank_supported(rank, name, [source]):
                return True
    return False


def normalize_label(row):
    result = copy.deepcopy(row)
    name = result.get("name")
    quotes = result.get("source_quotes") or [result.get("source_quote", "")]
    if not isinstance(name, str) or not isinstance(quotes, list):
        return result
    # 兼容历史把角色拼入name的写法；只有原文明确时才分离，不推断角色。
    for role, scope in ROLES.items():
        match = re.fullmatch(r"(.+?)(?:\s+|[（(])" + re.escape(role) + r"[）)]?", name)
        if not match or result.get("scope") != scope:
            continue
        identity = match[1].strip()
        if any(positive_role(source, role) for source in local_sources(identity, quotes)):
            if result.get("candidate_role", role) != role:
                raise ValueError("候选角色与原声明不一致")
            result["name"] = identity
            result.setdefault("display_name", name)
            result["candidate_role"] = role
            name = identity
            break
    role, display = result.get("candidate_role"), result.get("display_name")
    if role is None and display is None:
        return result
    sources = list(local_sources(name, quotes))
    if not sources:
        raise ValueError("候选名称缺少同条来源，不能绑定展示名")
    declared_roles = {role for role in ROLES if any(positive_role(source, role) for source in sources)}
    supported_roles = {role for role in declared_roles if ROLES[role] == result.get("scope")}
    if declared_roles and not supported_roles:
        raise ValueError("候选scope与同条来源中的角色不一致")
    if role is not None:
        if not isinstance(role, str) or ROLES.get(role) != result.get("scope"):
            raise ValueError("候选角色或scope声明不一致")
        if role not in supported_roles:
            raise ValueError("候选角色缺少同条来源支持")
    elif len(supported_roles) == 1:
        role = result["candidate_role"] = next(iter(supported_roles))
    if display is None:
        result["display_name"] = name + " " + role
        return result
    if not isinstance(display, str) or not display.strip():
        raise ValueError("候选展示名须为非空文本")
    display_roles = {r for r, words in ROLE_WORDS.items() if any(w in display for w in words)}
    if display_roles - supported_roles:
        raise ValueError("候选展示名中的角色缺少同条来源支持")
    if not _display_supported(display, name, role, result.get("rank"), sources):
        raise ValueError("候选展示名须对应同条来源中的材料或简称，不能新增科学结论")
    return result
