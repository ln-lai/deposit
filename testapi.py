#encoding=utf-8
# pip install zhipuai 请先在终端进行安装
from zhipuai import ZhipuAI
import time
client = ZhipuAI(api_key="4ff7309af7b648bf9978029246a04c4d.6zQwsrUtNt2BqDoP")
def get_completion(prompt: str, history=None):
    if history is None:
        history = []
    history.append({"role": "user", "content": prompt})
    response = client.chat.asyncCompletions.create(
        model="glm-4-flash",
        messages=history,
    )
    task_id = response.id
    task_status = ''
    get_cnt = 0
    while task_status != 'SUCCESS' and task_status != 'FAILED' and get_cnt <= 40:
        result_response = client.chat.asyncCompletions.retrieve_completion_result(id=task_id)
        task_status = result_response.task_status
        time.sleep(.5)
        get_cnt += 1
    content = result_response.choices[0].message.content
    history.append({"role": "assistant", "content": content})
    return content,history

if __name__ == '__main__':
    print(111)
    response, history = get_completion('用python写一个爱心')
    print(response)
    print(history)