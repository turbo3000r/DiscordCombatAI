import os
import random
import string
import typing
import discord

from modules.guild import Guild
from modules.AIHandler import AIHandler
from modules.LoggerHandler import get_logger
logger = get_logger()

def random_string(length: int) -> str:
    return ''.join(random.choice(string.printable.replace(string.whitespace, '')) for _ in range(length))

class Prompt:
    def __init__(self, content: str):
        self.content = content

    def fill(self, **kwargs) -> "Prompt":
        return Prompt(self.content.format(**kwargs))
    
    def __add__(self, other: "Prompt") -> "Prompt":
        return Prompt(self.content+"\n---\n"+other.content)
    
    def to_dict(self) -> dict:
        return {"content": self.content}

    def __str__(self):
        return self.content
    def __repr__(self):
        return self.__str__()


class SystemPrompt(Prompt):
    def __init__(self, path: str):
        with open(path, "r") as f:
            super().__init__(f.read())


class RandomSystemPrompt(SystemPrompt):
    def __init__(self, prompts: list[SystemPrompt]):
        self.prompts = prompts
    @property
    def content(self) -> str:
        return str(random.choice(self.prompts)) 


class Prompts:
    class Core:
        SimpleBattle = SystemPrompt(r"prompts\core\core_simple_battle.txt")
        EnvironmentCombiner = SystemPrompt(r"prompts\core\environment_combiner.txt")
        GenericEnvironment = RandomSystemPrompt([
            SystemPrompt(r"prompts\core\generic_environments\generic_environment0.txt"), 
            SystemPrompt(r"prompts\core\generic_environments\generic_environment1.txt")
        ])
    class Setting:
        UnpredictableRealistic = SystemPrompt(r"prompts\setting\unpredictable-realistic.txt")
        UnpredictableFunny = SystemPrompt(r"prompts\setting\unpredictable-funny.txt")
        UnpredictableDreamcore = SystemPrompt(r"prompts\setting\unpredictable-dreamcore.txt")
        Dreamcore = SystemPrompt(r"prompts\setting\dreamcore.txt")
        Realistic = SystemPrompt(r"prompts\setting\realistic.txt")
        RealisticUrban = SystemPrompt(r"prompts\setting\realistic-urban.txt")
        RealisticNature = SystemPrompt(r"prompts\setting\realistic-nature.txt")
    class Elements:
        CustomEnvironment = SystemPrompt(r"prompts\elements\custom_environment.txt")
        Language = SystemPrompt(r"prompts\elements\language.txt")

SETTINGS = {
    "unpredictable-dreamcore": Prompts.Setting.UnpredictableDreamcore,
    "unpredictable-funny": Prompts.Setting.UnpredictableFunny,
    "dreamcore": Prompts.Setting.Dreamcore,
    "realistic": Prompts.Setting.Realistic,
    "realistic-urban": Prompts.Setting.RealisticUrban,
    "realistic-nature": Prompts.Setting.RealisticNature,
}

class PromptHandler:
    def __init__(self, guild: Guild, ai_handler: AIHandler):
        self.guild = guild
        self.ai_handler = ai_handler

    
    @classmethod
    def from_guild(cls, guild: Guild) -> "PromptHandler":
        return cls(guild, guild.AIHandler)

    async def evaluateSingle(self, system_prompt: typing.Union[SystemPrompt, Prompt], prompt: str, temperature: float = 1.2) -> str:
        logger.debug(f"Evaluating single prompt: {system_prompt} with prompt: {prompt}")
        return await self.ai_handler.generate_response(system_instruction=str(system_prompt), prompt=prompt, temperature=1.2)

    async def evaluateMultiple(self, system_prompts: list[typing.Union[SystemPrompt, Prompt]], prompt: typing.Union[SystemPrompt, Prompt]) -> str:
        combined_system_prompt = system_prompts[0]
        for sp in system_prompts[1:]:
            combined_system_prompt = combined_system_prompt + sp
        return await self.evaluateSingle(combined_system_prompt, prompt)
#from dotenv import load_dotenv
#load_dotenv()
#AIHandler = AIHandler(api_key=os.getenv("AI_TOKEN"), model=os.getenv("MODEL"))


#prompt = Prompts.Core.GenericEnvironment
#env = Prompts.Elements.CustomEnvironment.fill(env="ENVIRonment X")
#Environmetn = "Shipwreck of the Titanic\n---\nDeep Sea\n---\nHelicopter"
#system_prompt = Prompts.Core.SimpleBattle + Prompts.Core.GenericEnvironment + Prompts.Setting.Realistic + Prompts.Elements.Language.fill(locale="uk-UA") 
#print(system_prompt)
#print(AIHandler.generate_response(system_instruction=str(system_prompt), prompt=Environmetn, temperature=1.2))