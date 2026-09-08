import re
from dataclasses import dataclass, field


@dataclass
class TerraformArgument:
    name: str
    required: bool
    description: str = ""
    type: str = "string"


@dataclass
class TerraformBlock:
    name: str
    required: bool = False
    arguments: list = field(default_factory=list)


@dataclass
class TerraformResource:
    resource_name: str
    provider: str
    description: str = ""
    required_args: list = field(default_factory=list)
    optional_args: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    example_hcl: str = ""
    file_sha: str = ""


class TerraformDocParser:
    """
    Parse les fichiers .html.markdown des providers Terraform.

    Structure attendue :
    # resource_name
    Description...
    ## Example Usage
    ```hcl ... ```
    ## Argument Reference
    * `arg_name` - (Required) Description...
    * `arg_name` - (Optional) Description...
    ### `block_name` block
    ## Attributes Reference
    ## Import
    """

    def parse(
        self,
        content: str,
        filename: str,
        provider: str,
        file_sha: str = "",
    ) -> TerraformResource:
        resource_name = self._filename_to_resource_name(filename, provider)

        resource = TerraformResource(
            resource_name=resource_name,
            provider=provider,
            file_sha=file_sha,
        )

        resource.description = self._extract_description(content)
        resource.example_hcl = self._extract_first_example(content)

        args, blocks = self._extract_arguments(content)
        resource.required_args = [a for a in args if a.required]
        resource.optional_args = [a for a in args if not a.required][:8]
        resource.blocks = blocks

        return resource

    def _filename_to_resource_name(self, filename: str, provider: str) -> str:
        """
        Ex: "virtual_network.html.markdown" → "azurerm_virtual_network"
        Ex: "s3_bucket.html.markdown" → "aws_s3_bucket"
        """
        name = filename.replace(".html.markdown", "").replace(".md", "")
        prefix = {
            "azurerm": "azurerm_",
            "aws": "aws_",
            "google": "google_",
        }.get(provider, f"{provider}_")

        if not name.startswith(prefix):
            return f"{prefix}{name}"
        return name

    def _extract_description(self, content: str) -> str:
        match = re.search(
            r"^# [^\n]+\n+(?:~>[^\n]+\n+)?([^#\n][^\n]+(?:\n[^#\n][^\n]+)*)",
            content,
            re.MULTILINE,
        )
        if match:
            desc = match.group(1).strip()
            desc = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", desc)
            return desc[:300]
        return ""

    def _extract_first_example(self, content: str) -> str:
        match = re.search(r"```hcl\n(.*?)```", content, re.DOTALL)
        if match:
            return match.group(1).strip()[:1000]
        match = re.search(r"```terraform\n(.*?)```", content, re.DOTALL)
        if match:
            return match.group(1).strip()[:1000]
        return ""

    def _extract_arguments(
        self, content: str
    ) -> tuple[list[TerraformArgument], list[TerraformBlock]]:
        arg_section_match = re.search(
            r"## Argument[s]? Reference(.*?)(?=## Attribute|## Import|## Timeouts|$)",
            content,
            re.DOTALL | re.IGNORECASE,
        )

        if not arg_section_match:
            return [], []

        section = arg_section_match.group(1)
        arguments = []
        blocks = []

        required_matches = re.findall(
            r"\* `([a-z_][a-z0-9_]*)` - \(Required(?:[^)]*)\)\s*([^\n*`]+)", section
        )
        for name, desc in required_matches:
            arguments.append(
                TerraformArgument(
                    name=name, required=True, description=desc.strip()[:120]
                )
            )

        optional_matches = re.findall(
            r"\* `([a-z_][a-z0-9_]*)` - \(Optional(?:[^)]*)\)\s*([^\n*`]+)", section
        )
        for name, desc in optional_matches:
            arguments.append(
                TerraformArgument(
                    name=name, required=False, description=desc.strip()[:120]
                )
            )

        block_matches = re.findall(
            r"###\s+(?:The\s+)?`([a-z_][a-z0-9_]*)` (?:block|configuration block)",
            section,
            re.IGNORECASE,
        )
        for block_name in block_matches:
            block_section_match = re.search(
                rf"### .*?`{re.escape(block_name)}`.*?\n(.*?)(?=###|##|$)",
                section,
                re.DOTALL,
            )
            block_args = []
            if block_section_match:
                block_text = block_section_match.group(1)
                req = re.findall(
                    r"\* `([a-z_][a-z0-9_]*)` - \(Required\)", block_text
                )
                block_args = []
                if block_section_match:
                    block_text = block_section_match.group(1)
                    req = re.findall(
                        r"\* `([a-z_][a-z0-9_]*)` - \(Required(?:[^)]*)\)\s*([^\n*`]*)", 
                        block_text
                    )
                    opt = re.findall(
                        r"\* `([a-z_][a-z0-9_]*)` - \(Optional(?:[^)]*)\)\s*([^\n*`]*)", 
                        block_text
                    )
                    block_args = [name for name, _ in req] + [name for name, _ in opt]

                    blocks.append(TerraformBlock(name=block_name, arguments=block_args))

        return arguments, blocks
