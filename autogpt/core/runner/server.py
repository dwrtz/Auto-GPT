import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel

from autogpt.core.messaging.simple import Message, Role, SimpleMessageBroker
from autogpt.core.runner.agent import agent_context
from autogpt.core.runner.factory import agent_factory_context

##################################################
# Hacking stuff together for an in-process model #
##################################################


class MessageFilters:
    @staticmethod
    def is_user_message(message: Message):
        metadata = message.metadata
        return metadata.sender.role == Role.USER

    @staticmethod
    def is_agent_message(message: Message):
        metadata = message.metadata
        return metadata.sender.role == Role.AGENT

    @staticmethod
    def is_agent_factory_message(message: Message):
        metadata = message.metadata
        return metadata.sender.role == Role.AGENT_FACTORY

    @staticmethod
    def is_server_message(message: Message):
        return MessageFilters.is_agent_message(
            message
        ) | MessageFilters.is_agent_factory_message(message)

    @staticmethod
    def is_user_bootstrap_message(message: Message):
        metadata = message.metadata
        return (
            MessageFilters.is_user_message(message)
            & metadata.additional_metadata["instruction"]
            == "bootstrap_agent"
        )

    @staticmethod
    def is_user_launch_message(message: Message):
        metadata = message.metadata
        return (
            MessageFilters.is_user_message(message)
            & metadata.additional_metadata["instruction"]
            == "launch_agent"
        )


class FakeApplicationServer:
    """The interface to the 'application server' process.

    This could be a restful API or something.

    """

    message_queue = defaultdict(list)

    def __init__(self):
        self._message_broker = self._get_message_broker()

        self._user_emitter = self._message_broker.get_emitter(
            channel_name="autogpt",
            sender_name="autogpt-user",
            sender_role=Role.USER,
        )

    def _get_message_broker(self) -> SimpleMessageBroker:
        message_channel_name = "autogpt"
        message_broker = SimpleMessageBroker()
        message_broker.create_message_channel(message_channel_name)

        message_broker.register_listener(
            message_channel="autogpt",
            listener=self._add_to_queue,
            message_filter=MessageFilters.is_server_message,
        )

        message_broker.register_listener(
            message_channel="autogpt",
            listener=agent_factory_context.bootstrap_agent,
            message_filter=MessageFilters.is_user_bootstrap_message,
        )

        message_broker.register_listener(
            message_channel="autogpt",
            listener=agent_context.launch_agent,
            message_filter=MessageFilters.is_user_launch_message,
        )

        return message_broker

    async def _add_to_queue(self, message: Message):
        self.message_queue[message.metadata.sender.name].append(message)

    async def _send_message(
        self,
        request,
        extra_content: dict = None,
        extra_metadata: dict = None,
    ):
        content = {**request.json["content"], **extra_content}
        metadata = {**request.json["metadata"], **extra_metadata}

        success = self._user_emitter.send_message(content, **metadata)
        response = object()
        if success:
            response.status_code = 200
        else:
            response.status_code = 500
        return response

    async def list_agents(self, request):
        """List all agents."""
        pass

    async def boostrap_new_agent(self, request):
        """Bootstrap a new agent."""
        response = await self._send_message(
            request,
            extra_content={"message_broker": self._message_broker},
            extra_metadata={"instruction": "bootstrap_agent"},
        )
        # Collate all responses from the agent factory since we're in-process.
        agent_factory_responses = self.message_queue["autogpt-agent-factory"]
        self.message_queue["autogpt-agent-factory"] = []
        response.json = agent_factory_responses
        return response

    async def launch_agent(self, request):
        """Launch an agent."""
        return await self._send_message(request)

    async def give_agent_feedback(self, request):
        """Give feedback to an agent."""
        response = await self._send_message(request)
        response.json = {
            "content": self.message_queue["autogpt-agent"].pop(),
        }

    # async def get_agent_plan(self, request):
    #     """Get the plan for an agent."""
    #     # TODO: need a clever hack here to get the agent plan since we'd have natural
    #     #  asynchrony here with a webserver.
    #     pass


application_server = FakeApplicationServer()


def _get_workspace_path_from_agent_name(agent_name: str) -> str:
    # FIXME: Very much a stand-in for later logic. This could be a whole agent registry
    #  system and probably lives on the client side instead of here
    return f"~/autogpt_workspace/{agent_name}"


def launch_agent(message: Message):
    message_content = message.content
    message_broker = message_content["message_broker"]
    agent_name = message_content["agent_name"]
    workspace_path = _get_workspace_path_from_agent_name(agent_name)

    agent = Agent.from_workspace(workspace_path, message_broker)
    agent.run()


###############
# HTTP SERVER #
###############

router = APIRouter()


class CreateAgentRequestBody(BaseModel):
    ai_name: str
    ai_role: str
    ai_goals: List[str]
    # could add more config as needed


class CreateAgentResponseBody(BaseModel):
    agent_id: str


@router.post("/agents", response_model=CreateAgentResponseBody)
async def create_agent(request: Request, body: CreateAgentRequestBody):
    """Create a new agent."""

    # validate request headers.
    api_key = request.headers.get("openai_api_key")
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="missing openai_api_key header key",
        )

    # this is where you would do something with the request body
    # ...

    # initialize the agent

    agent_id = uuid.uuid4().hex

    return {"agent_id": agent_id}


class InteractRequestBody(BaseModel):
    user_input: Optional[str] = None


class Command(BaseModel):
    name: str
    args: Dict[str, str]


class Thoughts(BaseModel):
    text: str
    reasoning: str
    plan: List[str] | str
    criticism: str
    speak: str


class AssistantReply(BaseModel):
    thoughts: Optional[Thoughts]
    command: Optional[Command]


class InteractResponseBody(BaseModel):
    result: Optional[str | List[str]]
    assistant: AssistantReply


@router.post("/agents/{agent_id}", response_model=InteractResponseBody)
async def interact(request: Request, agent_id: str, body: InteractRequestBody):
    """Interact with an agent."""

    # validate request headers.
    api_key = request.headers.get("openai_api_key")
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="missing openai_api_key header key",
        )

    # check if agent_id exists

    # get agent object from somewhere, e.g. a database/disk/global dict

    # continue agent interaction with user input

    return {
        "result": "Command write_to_file returned: File written to successfully.",
        "assistant": {
            "thoughts": {
                "text": "My goal has been achieved, so I will use the 'task_complete' command to shut down.",
                "reasoning": "Since my goal has been achieved, there is no need to perform any further actions.",
                "plan": "- Use 'task_complete' command to shut down",
                "criticism": "I did not consider any alternative plans or potential issues that may arise.",
                "speak": "I have completed my task and will now shut down.",
            },
            "command": {
                "name": "task_complete",
                "args": {"reason": "Message has been written to file."},
            },
        },
    }


class AiConfig(BaseModel):
    ai_name: str
    ai_role: str
    ai_goals: List[str]


class ListAgentsItem(BaseModel):
    agent_id: str
    ai_config: AiConfig
    status: str
    created_at: int
    updated_at: int


class ListAgentsResponseBody(BaseModel):
    agents: List[ListAgentsItem]


@router.get("/agents", response_model=ListAgentsResponseBody)
async def list_agents(request: Request):
    """List all agents."""
    return {
        "agents": [
            {
                "agent_id": "b50932f1da8148a092736066b4cdc432",
                "ai_config": {
                    "ai_name": "HelloBot",
                    "ai_role": "An AI that says 'Hello, World!'",
                    "ai_goals": [
                        "Write your message in a file called 'message.txt'.",
                        "Shut down.",
                    ],
                },
                "status": "completed",
                "created_at": 1684173110491,
                "updated_at": 1684173468437,
            },
            {
                "agent_id": "1b0a56f805d244118117dd55e89022f9",
                "ai_config": {
                    "ai_name": "HelloBot",
                    "ai_role": "An AI that says 'Hello, World!'",
                    "ai_goals": [
                        "Write your message in a file called 'message.txt'.",
                        "Shut down.",
                    ],
                },
                "status": "active",
                "created_at": 1684030923958,
                "updated_at": 1684030940859,
            },
            {
                "agent_id": "76ec74bf16544ba3a115258f6d52b7c5",
                "ai_config": {
                    "ai_name": "HelloBot",
                    "ai_role": "An AI that says 'Hello, World!'",
                    "ai_goals": [
                        "Write your message in a file called 'message.txt'.",
                        "Shut down.",
                    ],
                },
                "status": "active",
                "created_at": 1683987345020,
                "updated_at": 1683987368569,
            },
            {
                "agent_id": "7c37ca1bf65b4bf5a00b43ed5612e2d4",
                "ai_config": {
                    "ai_name": "HelloBot",
                    "ai_role": "An AI that says 'Hello, World!'",
                    "ai_goals": [
                        "Write your message in a file called 'message.txt'.",
                        "Shut down.",
                    ],
                },
                "status": "active",
                "created_at": 1683949058448,
                "updated_at": 1683949111032,
            },
            {
                "agent_id": "0790ab86ad9f4271be4fce0ed6e05dbb",
                "ai_config": {
                    "ai_name": "HelloBot",
                    "ai_role": "An AI that says 'Hello, World!'",
                    "ai_goals": [
                        "Write your message in a file called 'message.txt'.",
                        "Shut down.",
                    ],
                },
                "status": "completed",
                "created_at": 1683748119231,
                "updated_at": 1683748158216,
            },
        ]
    }


class InteractHistoryItem(BaseModel):
    created_at: int
    response: InteractResponseBody


class InteractHistoryResponseBody(BaseModel):
    history: List[InteractHistoryItem]


@router.get("/agents/{agent_id}", response_model=InteractHistoryResponseBody)
async def interact_history(request: Request, agent_id: str):
    """Get the interaction history for an agent."""
    return {
        "history": [
            {
                "created_at": 1684173128860,
                "response": {
                    "result": None,
                    "assistant": {
                        "thoughts": {
                            "text": "My goal is to write a message to a file called 'message.txt' and then shut down. The simplest way to achieve this is to use the 'write_to_file' command to write the message and then use the 'task_complete' command to shut down. I will proceed with this plan.",
                            "speak": "I will write the message to a file and then shut down.",
                            "plan": "- Use 'write_to_file' command to write message to 'message.txt'\n- Use 'task_complete' command to shut down",
                            "criticism": "I did not consider any alternative plans or potential issues that may arise.",
                            "reasoning": "I have analyzed my goal and determined the most efficient way to achieve it.",
                        },
                        "command": {
                            "args": {"text": "Hello, World!", "file": "message.txt"},
                            "name": "write_to_file",
                        },
                    },
                },
            },
            {
                "created_at": 1684173321180,
                "response": {
                    "result": "Command write_to_file returned: File written to successfully.",
                    "assistant": {
                        "thoughts": {
                            "text": "My goal has been achieved, so I will use the 'task_complete' command to shut down.",
                            "speak": "I have completed my task and will now shut down.",
                            "plan": "- Use 'task_complete' command to shut down",
                            "criticism": "I did not consider any alternative plans or potential issues that may arise.",
                            "reasoning": "Since my goal has been achieved, there is no need to perform any further actions.",
                        },
                        "command": {
                            "args": {"reason": "Message has been written to file."},
                            "name": "task_complete",
                        },
                    },
                },
            },
            {
                "created_at": 1684173468819,
                "response": {
                    "result": "Shutting down.",
                    "assistant": {"thoughts": None, "command": None},
                },
            },
        ]
    }


app = FastAPI()
app.include_router(router, prefix="/api/v1")
# NOTE:
# - start with `uvicorn autogpt.core.runner.server:app --reload --port=8080`
# - see auto-generated API docs: http://localhost:8080/docs
