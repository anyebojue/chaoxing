import configparser
import requests
from pathlib import Path
import json
from api.logger import logger
import random
from urllib3 import disable_warnings,exceptions
from openai import OpenAI
import httpx
from re import sub
# 关闭警告
disable_warnings(exceptions.InsecureRequestWarning)

class CacheDAO:
    """
    @Author: SocialSisterYi
    @Reference: https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy
    """
    def __init__(self, file: str = "cache.json"):
        self.cacheFile = Path(file)
        if not self.cacheFile.is_file():
            self.cacheFile.open("w").write("{}")
        self.fp = self.cacheFile.open("r+", encoding="utf8")

    def getCache(self, question: str):
        self.fp.seek(0)
        data = json.load(self.fp)
        if isinstance(data, dict):
            return data.get(question)

    def addCache(self, question: str, answer: str):
        self.fp.seek(0)
        data: dict = json.load(self.fp)
        data[question] = answer
        self.fp.seek(0)
        json.dump(data, self.fp, ensure_ascii=False, indent=4)


class Tiku:
    CONFIG_PATH = "config.ini"  # 默认配置文件路径
    DISABLE = False     # 停用标志
    SUBMIT = False      # 提交标志
    COVER_RATE = 0.8    # 覆盖率

    def __init__(self) -> None:
        self._name = None
        self._api = None
        self._conf = None

    @property
    def name(self):
        return self._name
    
    @name.setter
    def name(self, value):
        self._name = value

    @property
    def api(self):
        return self._api
    
    @api.setter
    def api(self, value):
        self._api = value

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self,value):
        self._token = value

    def init_tiku(self):
        # 仅用于题库初始化, 应该在题库载入后作初始化调用, 随后才可以使用题库
        # 尝试根据配置文件设置提交模式
        if not self._conf:
            self.config_set(self._get_conf())
        if not self.DISABLE:
            # 设置提交模式
            self.SUBMIT = True if self._conf['submit'] == 'true' else False
            self.COVER_RATE = float(self._conf['cover_rate'])
            # 调用自定义题库初始化
            self._init_tiku()
        
    def _init_tiku(self):
        # 仅用于题库初始化, 例如配置token, 交由自定义题库完成
        pass

    def config_set(self,config):
        self._conf = config

    def _get_conf(self):
        """
        从默认配置文件查询配置, 如果未能查到, 停用题库
        """
        try:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_PATH, encoding="utf8")
            return config['tiku']
        except (KeyError, FileNotFoundError):
            logger.info("未找到tiku配置, 已忽略题库功能")
            self.DISABLE = True
            return None

    def query(self,q_info:dict):
        if self.DISABLE:
            return None

        # 预处理, 去除【单选题】这样与标题无关的字段
        # 此处需要改进！！！
        logger.debug(f"原始标题：{q_info['title']}")
        q_info['title'] = sub(r'^\d+', '', q_info['title'])
        q_info['title'] = sub(r'^(?:【.*?】)+', '', q_info['title'])
        q_info['title'] = sub(r'（\d+\.\d+分）$', '', q_info['title'])
        logger.debug(f"处理后标题：{q_info['title']}")

        # 先过缓存
        cache_dao = CacheDAO()
        answer = cache_dao.getCache(q_info['title'])
        if answer:
            logger.info(f"从缓存中获取答案：{q_info['title']} -> {answer}")
            return answer.strip()
        else:
            answer = self._query(q_info)
            if answer:
                answer = answer.strip()
                cache_dao.addCache(q_info['title'], answer)
                logger.info(f"从{self.name}获取答案：{q_info['title']} -> {answer}")
                return answer
            logger.error(f"从{self.name}获取答案失败：{q_info['title']}")
        return None
    
    def _query(self,q_info:dict):
        """
        查询接口, 交由自定义题库实现
        """
        pass

    def get_tiku_from_config(self):
        """
        从配置文件加载题库, 这个配置可以是用户提供, 可以是默认配置文件
        """
        if not self._conf:
            # 尝试从默认配置文件加载
            self.config_set(self._get_conf())
        if self.DISABLE:
            return self
        try:
            cls_name = self._conf['provider']
            if not cls_name:
                raise KeyError
        except KeyError:
            self.DISABLE = True
            logger.error("未找到题库配置, 已忽略题库功能")
            return self
        new_cls = globals()[cls_name]()
        new_cls.config_set(self._conf)
        return new_cls
    
    def jugement_select(self,answer:str) -> bool:
        """
        这是一个专用的方法, 要求配置维护两个选项列表, 一份用于正确选项, 一份用于错误选项, 以应对题库对判断题答案响应的各种可能的情况
        它的作用是将获取到的答案answer与可能的选项列对比并返回对应的布尔值
        """
        if self.DISABLE:
            return False
        true_list = self._conf['true_list'].split(',')
        false_list = self._conf['false_list'].split(',')
        # 对响应的答案作处理
        answer = answer.strip()
        # 精确匹配
        if answer in true_list:
            return True
        elif answer in false_list:
            return False
        # 在全文本中搜索关键词（处理完整解释的情况，如 "错误：xxx"）
        for kw in true_list:
            if kw in answer:
                return True
        for kw in false_list:
            if kw in answer:
                return False
        else:
            # 无法判断, 随机选择
            logger.error(f'无法判断答案 -> {answer} 对应的是正确还是错误, 请自行判断并加入配置文件重启脚本, 本次将会随机选择选项')
            return random.choice([True,False])
    
    def get_submit_params(self):
        """
        这是一个专用方法, 用于根据当前设置的提交模式, 响应对应的答题提交API中的pyFlag值
        """
        # 留空直接提交, 1保存但不提交
        if self.SUBMIT:
            return ""
        else:
            return "1"

# 按照以下模板实现更多题库

class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = '言溪题库'
        self.api = 'https://tk.enncy.cn/query'
        self._token = None
        self._token_index = 0   # token队列计数器
        self._times = 100   # 查询次数剩余, 初始化为100, 查询后校对修正

    def _query(self,q_info:dict):
        res = requests.get(
            self.api,
            params={
                'question':q_info['title'],
                'token':self._token
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            if not res_json['code']:
                # 如果是因为TOKEN次数到期, 则更换token
                if self._times == 0 or '次数不足' in res_json['data']['answer']:
                    logger.info(f'TOKEN查询次数不足, 将会更换并重新搜题')
                    self._token_index += 1
                    self.load_token()
                    # 重新查询
                    return self._query(q_info)
                logger.error(f'{self.name}查询失败:\n\t剩余查询数{res_json["data"].get("times",f"{self._times}(仅参考)")}:\n\t消息:{res_json["message"]}')
                return None
            self._times = res_json["data"].get("times",self._times)
            return res_json['data']['answer'].strip()
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
        return None
    
    def load_token(self): 
        token_list = self._conf['tokens'].split(',')
        if self._token_index == len(token_list):
            # TOKEN 用完
            logger.error('TOKEN用完, 请自行更换再重启脚本')
            raise Exception(f'{self.name} TOKEN 已用完, 请更换')
        self._token = token_list[self._token_index]

    def _init_tiku(self):
        self.load_token()

class TikuLike(Tiku):
    # Like知识库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = 'Like知识库'
        self.ver = '1.0.8' #对应官网API版本
        self.query_api = 'https://api.datam.site/search'
        self.balance_api = 'https://api.datam.site/balance'
        self.homepage = 'https://www.datam.site'
        self._model = None
        self._token = None
        self._times = -1
        self._search = False
        self._count = 0

    def _query(self,q_info:dict):
        q_info_map = {"single":"【单选题】","multiple":"【多选题】","completion":"【填空题】","judgement":"【判断题】"}
        api_params_map = {0:"others",1:"choose",2:"fills",3:"judge"}
        q_info_prefix = q_info_map.get(q_info['type'],"【其他类型题目】")
        options = ', '.join(q_info['options']) if isinstance(q_info['options'], list) else q_info['options']
        question = "{}{}\n{}".format(q_info_prefix,q_info['title'],options)
        ret = ""
        ans = ""
        res = requests.post(
            self.query_api,
            json={
                'query': question,
                'token': self._token,
                'model': self._model if self._model else '',
                'search': self._search
            },
            verify=False
        )

        if res.status_code == 200:
            res_json = res.json()
            q_type = res_json['data'].get('type',0)
            params = api_params_map.get(q_type,"")
            ans = res_json['data'].get(params,"")
            if q_type == 3:
                ans = "正确" if ans ==1 else "错误"
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
            return None

        ret += str(ans)

        self._times -= 1

        #10次查询后更新实际次数
        self._count = (self._count+1) % 10

        if self._count == 0:
            self.update_times()
        
        return ret
    
    def update_times(self):
        res = requests.post(
            self.balance_api,
            json={
                'token': self._token,
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            self._times = res_json["data"].get("balance",self._times)
            logger.info("当前LIKE知识库Token剩余查询次数为: {}".format(str(self._times)))
        else:
            logger.error('TOKEN出现错误，请检查后再试')

    def load_token(self): 
        token = self._conf['tokens'].split(',')[-1] if ',' in self._conf['tokens'] else self._conf['tokens']
        self._token = token

    def load_config(self):
        var_params = {"likeapi_search":self._search,"likeapi_model":self._model}
        config_params = {"likeapi_search":False, "likeapi_model":None}

        for k,v in config_params.items():
            if k in self._conf:
                var_params[k] = self._conf[k]
            else:
                var_params[k] = v

    def _init_tiku(self):
        self.load_token()
        self.load_config()
        self.update_times()

class TikuAdapter(Tiku):
    # TikuAdapter题库实现 https://github.com/DokiDoki1103/tikuAdapter
    def __init__(self) -> None:
        super().__init__()
        self.name = 'TikuAdapter题库'
        self.api = ''

    def _query(self, q_info: dict):
        # 判断题目类型
        if q_info['type'] == "single":
            type = 0
        elif q_info['type'] == 'multiple':
            type = 1
        elif q_info['type'] == 'completion':
            type = 2
        elif q_info['type'] == 'judgement':
            type = 3
        else:
            type = 4

        options = q_info['options']
        res = requests.post(
            self.api,
            json={
                'question': q_info['title'],
                'options': [sub(r'^[A-Za-z]\.?、?\s?', '', option) for option in options.split('\n')],
                'type': type
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            # if bool(res_json['plat']):
            # plat无论搜没搜到答案都返回0
            # 这个参数是tikuadapter用来设定自定义的平台类型
            if not len(res_json['answer']['allAnswer']):
                logger.error("查询失败, 返回：" + res.text)
                return None
            sep = "\n"
            return sep.join(res_json['answer']['allAnswer'][0]).strip()
        # else:
        #   logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def _init_tiku(self):
        # self.load_token()
        self.api = self._conf['url']


class TikuItihey(Tiku):
    # itihey.com 题库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = 'itihey题库'
        self.search_api = 'https://itihey.com/web-service/v1/search'
        self.answer_api = 'https://itihey.com/web-service/v1/answer'
        self._token = None

    def _init_tiku(self):
        # 从配置中读取 token
        tokens = self._conf.get('tokens', '')
        if not tokens:
            logger.warning(f"{self.name} 未配置 tokens，答案功能将被禁用")
            self.DISABLE = True
        else:
            # 支持多 token 逗号分隔，取第一个
            self._token = tokens.split(',')[0].strip()
            if not self._token:
                self.DISABLE = True

    def _query(self, q_info: dict):
        if not self._token:
            return None

        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
        })
        session.cookies.set('access-token', self._token, domain='itihey.com')

        # 搜索题目
        search_res = session.post(
            self.search_api,
            json={'question': q_info['title'], 'pageSize': 3, 'pageNo': 1},
            verify=False, timeout=15
        )
        if search_res.status_code != 200:
            logger.error(f"{self.name} 搜索失败 (HTTP {search_res.status_code}): {search_res.text[:200]}")
            return None

        try:
            questions = search_res.json()
            if not questions:
                logger.error(f"{self.name} 未找到相关题目: {q_info['title'][:50]}")
                return None
        except Exception as e:
            logger.error(f"{self.name} 解析搜索结果失败: {e}")
            return None

        # 精确匹配：找题目文本完全相同的
        best_match = None
        for q in questions:
            if q.get('question') == q_info['title']:
                best_match = q
                break

        # 没找到完全匹配的，取第一个（可能是模糊匹配）
        if not best_match and questions:
            best_match = questions[0]

        if not best_match:
            return None

        # 获取答案
        answer_res = session.get(
            self.answer_api,
            params={'id': best_match['id'], 'source': best_match.get('source', 'v1')},
            verify=False, timeout=15
        )

        if answer_res.status_code == 200:
            try:
                answer_data = answer_res.json()
                if isinstance(answer_data, list) and answer_data:
                    answer_str = '\n'.join(str(a) for a in answer_data).strip()
                    logger.info(f"{self.name} 查询成功: {q_info['title'][:30]} -> {answer_str}")
                    return answer_str
                elif isinstance(answer_data, str) and answer_data:
                    return answer_data.strip()
            except Exception:
                pass
            logger.error(f"{self.name} 解析答案失败: {answer_res.text[:200]}")
        elif answer_res.status_code == 401:
            logger.error(f"{self.name} Token无效或已过期，请重新扫码登录获取 access-token")
        else:
            logger.error(f"{self.name} 获取答案失败 (HTTP {answer_res.status_code}): {answer_res.text[:200]}")
        return None


class TikuDaxue(Tiku):
    # 大学搜题王 (daxuesoutijiang.com) 题库实现
    # 答案直接从 SSE 流中的 notification 事件获取，无需额外调用 getAnswer
    def __init__(self) -> None:
        super().__init__()
        self.name = '大学搜题王'
        self.search_api = 'https://www.daxuesoutijiang.com/dxkits/aisearch/web/askstream'
        self._dxuss = None
        self._session_id = None

    def _init_tiku(self):
        tokens = self._conf.get('tokens', '')
        if not tokens:
            logger.warning(f"{self.name} 未配置 tokens，答案功能将被禁用")
            self.DISABLE = True
        else:
            parts = tokens.split(',')
            self._dxuss = parts[0].strip()
            self._session_id = parts[1].strip() if len(parts) > 1 else ''
            if not self._dxuss:
                self.DISABLE = True

    def _query(self, q_info: dict):
        if not self._dxuss:
            return None

        import time as time_module

        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
            'Accept': 'text/event-stream',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
            'Origin': 'https://www.daxuesoutijiang.com',
            'Referer': 'https://www.daxuesoutijiang.com/ai-chat',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-Dest': 'empty',
            'sec-ch-ua': '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Priority': 'u=1, i',
        })
        session.cookies.set('DXUSS', self._dxuss, domain='.daxuesoutijiang.com')
        session.cookies.set('Hm_lvt_bad2e71c01dba2e916b445b101d797ff', '1779071554', domain='.daxuesoutijiang.com')
        session.cookies.set('HMACCOUNT', '77DD9280A32566F1', domain='.daxuesoutijiang.com')

        question_text = q_info['title']
        if q_info.get('options'):
            opts = q_info['options']
            if isinstance(opts, str):
                opts = opts.split('\n')
            question_text += '\n' + '\n'.join(opts)

        local_msg_id = f"{int(time_module.time() * 1000)}-{random.randint(100, 999)}"
        payload = (
            f'source=aitab&userType=1&questionType=1&vc=1&appId=collegepcpi&scene=1'
            f'&localMsgId={local_msg_id}&sessionId={self._session_id}&chatPageFrom=collegepcpi'
            f'&ext=%7B%22editFlag%22%3A%220%22%2C%22questionId%22%3A%22%22%7D'
            f'&questionData=%7B%22text%22%3A%22'
            + question_text.replace('"', '\\"').replace('\n', '\\n')
            + '%22%7D&messageCategory=400'
        )

        answer_chunks = []
        current_event = None

        try:
            resp = session.post(
                self.search_api,
                data=payload.encode('utf-8'),
                verify=False,
                timeout=20,
                stream=True
            )

            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    line_str = line.decode('utf-8', errors='ignore')
                    if line_str.startswith('event:'):
                        current_event = line_str[6:].strip()
                        continue
                    if line_str.startswith('data:'):
                        raw = line_str[5:].strip()
                        if not raw or raw == 'null':
                            continue
                        data = json.loads(raw)
                        d = data.get('data', {})
                        if current_event == 'recognition':
                            answer_text = d.get('answerText', d.get('text', ''))
                            if answer_text:
                                answer_chunks.append(answer_text)
                        elif current_event == 'notification':
                            c = d.get('content', {})
                            if isinstance(c, dict):
                                text = c.get('text', '')
                            else:
                                text = str(c)
                            # 处理转义
                            text = text.replace('\\n', '\n').replace('\n', '\n')
                            if text:
                                answer_chunks.append(text)
                        elif current_event == 'finish':
                            break
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"{self.name} 搜索失败: {e}")
            return None

        if not answer_chunks:
            logger.error(f"{self.name} 未获取到答案")
            return None

        full_answer = ''.join(answer_chunks).strip()
        if not full_answer:
            return None

        # 提取答案：从 "# 块" 中找 "答案" 行，然后提取选项字母
        # 格式可能是 "B：1.5"、"B" 或 "B."
        colon_chars = ':：'
        # 先在答案块中找
        answer_letter = None
        in_answer_block = False
        found_answer_keyword = False
        for line in full_answer.split('\n'):
            stripped = line.strip()
            if stripped == '#':
                in_answer_block = True
                found_answer_keyword = False
                continue
            if in_answer_block and stripped in ('答案', '答案：', '答案:'):
                found_answer_keyword = True
                continue
            if in_answer_block and found_answer_keyword:
                if not stripped or stripped in ('解析', '解析：', '解析:'):
                    break
                # 格式 "C：绝对星等" 或 "C：1.5"
                if len(stripped) >= 2 and stripped[0] in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' and stripped[1] in colon_chars:
                    answer_letter = stripped[0]
                    break
                # 格式 "C" 或 "C."
                if stripped.upper() in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' and len(stripped) <= 2:
                    answer_letter = stripped.upper()
                    break

        if answer_letter:
            logger.info(f"{self.name} 查询成功: {q_info['title'][:30]} -> {answer_letter}")
            return answer_letter

        # 如果答案块没找到，在整个回答中查找 "X：内容" 格式
        for line in full_answer.split('\n'):
            stripped = line.strip()
            if len(stripped) >= 2 and stripped[0] in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' and stripped[1] in colon_chars:
                # 确认这是答案行（后面跟内容，不是单纯的 "A：")
                content = stripped[2:].strip()
                if content:
                    answer_letter = stripped[0]
                    logger.info(f"{self.name} 查询成功: {q_info['title'][:30]} -> {answer_letter}")
                    return answer_letter
                # 可能是格式 "A：" 单独一行，需要看下一行
                for next_line in full_answer.split('\n'):
                    next_stripped = next_line.strip()
                    if next_stripped and next_stripped not in ('#', '解析', '答案', '答案：', '答案:'):
                        # 下一行有内容，把它当成答案内容
                        answer_letter = stripped[0]
                        logger.info(f"{self.name} 查询成功: {q_info['title'][:30]} -> {answer_letter}")
                        return answer_letter

        # 最后兜底：直接返回整个回答内容
        logger.info(f"{self.name} 查询成功: {q_info['title'][:30]} -> {full_answer[:50]}")
        return full_answer


class AI(Tiku):
    # AI大模型答题实现
    def __init__(self) -> None:
        super().__init__()
        self.name = 'AI大模型答题'

    def _query(self, q_info: dict):
        if self.http_proxy:
            proxy = self.http_proxy
            httpx_client = httpx.Client(proxy=proxy)
            client = OpenAI(http_client=httpx_client, base_url = self.endpoint,api_key = self.key)
        else:
            client = OpenAI(base_url = self.endpoint,api_key = self.key)
        # 判断题目类型
        if q_info['type'] == "single":
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为单选题，你只能选择一个选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，特别注意回答的内容需要去除选项内容前的字母，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}\n选项：{q_info['options']}"
                    }
                ]
            )
        elif q_info['type'] == 'multiple':
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为多选题，你必须选择两个或以上选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，特别注意回答的内容需要去除选项内容前的字母，示例回答：{\"Answer\": [\"答案1\",\n\"答案2\",\n\"答案3\"]}。除此之外不要输出任何多余的内容。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}\n选项：{q_info['options']}"
                    }
                ]
            )
        elif q_info['type'] == 'completion':
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为填空题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}"
                    }
                ]
            )
        elif q_info['type'] == 'judgement':
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为判断题，你只能回答正确或者错误，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"正确\"]}。除此之外不要输出任何多余的内容。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}"
                    }
                ]
            )
        else:
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": "本题为简答题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"这是我的答案\"]}。除此之外不要输出任何多余的内容。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": f"题目：{q_info['title']}"
                    }
                ]
            )

        try:
            response = json.loads(completion.choices[0].message.content)
            sep = "\n"
            return sep.join(response['Answer']).strip()
        except:
            logger.error("无法解析大模型输出内容")
            return None

    def _init_tiku(self):
        self.endpoint = self._conf['endpoint']
        self.key = self._conf['key']
        self.model = self._conf['model']
        self.http_proxy = self._conf['http_proxy']
