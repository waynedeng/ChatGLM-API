import os
import uvicorn
import json
import traceback
import uuid
import argparse

from os.path import abspath, dirname
from loguru import logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from message_store import MessageStore
import torch

from utils import ModelArguments, load_pretrained
from transformers import AutoConfig, AutoModel, AutoTokenizer, HfArgumentParser
from errors import Errors
import knowledge
import gen_data

log_folder = os.path.join(abspath(dirname(__file__)), "log")
logger.add(os.path.join(log_folder, "{time}.log"), level="INFO")


DEFAULT_DB_SIZE = 100000

massage_store = MessageStore(db_path="message_store.json", table_name="chatgpt", max_size=DEFAULT_DB_SIZE)
# Timeout for FastAPI
# service_timeout = None

app = FastAPI()

origins = [
    "http://localhost",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
    "http://*.rccchina.com",
    "https://*.rccchina.com",
    "http://*.api.rccchina.com",
    "https://*.api.rccchina.com"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

stream_response_headers = {
    "Content-Type": "application/octet-stream",
    "Cache-Control": "no-cache",
}


@app.post("/config")
async def config():
    return JSONResponse(content=dict(
        message=None,
        status="Success",
        data=dict()
    ))



async def process(prompt, options, params, message_store, is_knowledge, history=None):
    """
    发文字消息
    """
    # 不能是空消息
    if not prompt:
        logger.error("Prompt is empty.")
        yield Errors.PROMPT_IS_EMPTY.value
        return


    try:
        chat = {"role": "user", "content": prompt}

        # 组合历史消息
        if options:
            parent_message_id = options.get("parentMessageId")
            messages = message_store.get_from_key(parent_message_id)
            if messages:
                messages.append(chat)
            else:
                messages = []
        else:
            parent_message_id = None
            messages = [chat]

        # 记忆
        messages = messages[-params['memory_count']:]


        history_formatted = []
        if options is not None:
            history_formatted = []
            tmp = []
            for i, old_chat in enumerate(messages):
                if len(tmp) == 0 and old_chat['role'] == "user":
                    tmp.append(old_chat['content'])
                elif old_chat['role'] == "AI":
                    tmp.append(old_chat['content'])
                    history_formatted.append(tuple(tmp))
                    tmp = []
                else:
                    continue

        uid = "chatglm"+uuid.uuid4().hex
        footer=''
        if is_knowledge:
            response_d = knowledge.find_whoosh(prompt)
            output_sources = [i['title'] for i in response_d]
            results ='\n---\n'.join([i['content'] for i in response_d])
            prompt=  f'system:基于以下内容，用中文简洁和专业回答用户的问题。\n\n'+results+'\nuser:'+prompt
            footer=  "\n参考：\n"+('\n').join(output_sources)+''
        # yield footer
        for response, history in model.stream_chat(tokenizer, prompt, history_formatted, max_length=params['max_length'],
                                                   top_p=params['top_p'], temperature=params['temperature']):
            message = json.dumps(dict(
                role="AI",
                id=uid,
                parentMessageId=parent_message_id,
                text=response+footer,
            ))
            yield "data: " + message

    except:
        err = traceback.format_exc()
        logger.error(err)
        yield Errors.SOMETHING_WRONG.value
        return

    try:
        # save to cache
        chat = {"role": "AI", "content": response}
        messages.append(chat)

        parent_message_id = uid
        message_store.set(parent_message_id, messages)
    except:
        err = traceback.format_exc()
        logger.error(err)


@app.post("/chat-process")
async def chat_process(request_data: dict):
    prompt = request_data['prompt']
    max_length = request_data['max_length']
    top_p = request_data['top_p']
    temperature = request_data['temperature']
    options = request_data['options']
    if request_data['memory'] == 1 :
        memory_count = 5
    elif request_data['memory'] == 50:
        memory_count = 20
    else:
        memory_count = 999

    if 1 == request_data["top_p"]:
        top_p = 0.2
    elif 50 == request_data["top_p"]:
        top_p = 0.5
    else:
        top_p = 0.9
    if temperature is None:
        temperature = 0.9
    if top_p is None:
        top_p = 0.7
    is_knowledge = request_data['is_knowledge']
    params = {
        "max_length": max_length,
        "top_p": top_p,
        "temperature": temperature,
        "memory_count": memory_count
    }
    answer_text = process(prompt, options, params, massage_store, is_knowledge)
    return StreamingResponse(content=answer_text, headers=stream_response_headers, media_type="text/event-stream")


if __name__ == "__main__":
    host = '0.0.0.0'
    port = 3002
    parser = HfArgumentParser(ModelArguments)
    model_args, = parser.parse_args_into_dataclasses()
    model, tokenizer = load_pretrained(model_args)
    model = model.cuda()
    model = model.eval()
    uvicorn.run(app, host=host, port=port)
