import requests
import datetime
from HallucinationTest.stack import Stack
from HallucinationTest.tag import Tag
import re

BPMN_MODEL_URI = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_URI     = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_URI         = "http://www.omg.org/spec/DD/20100524/DC"
DI_URI         = "http://www.omg.org/spec/DD/20100524/DI"
XSI_URI        = "http://www.w3.org/2001/XMLSchema-instance"
XSD_URI        = "http://www.w3.org/2001/XMLSchema"

KNOWN_NON_BPMN_PREFIXES_FALLBACK = {
    "bpmndi", "dc", "di", "omgdc", "omgdi", "xsi", "xsd",
}


class check:
    def __init__(self, texts):
        self.ErrorList = []
        self.WarningList = []
        self.ActivityList = []
        self.line0 = []
        self.line_tag = []
        self._raw_text = texts
        if texts is None or not str(texts).strip():
            self.ErrorList.append("Input XML text is empty or None.")
            self._malformed_xml_error = "Input XML text is empty or None."
        else:
            self._malformed_xml_error = None
            self.ToTag(texts)
            self.CheckPair(self.line_tag)
            self.CheckStructure(self.line_tag)
        for i in self.ErrorList:
            print("错误： " + i)
        for w in self.WarningList:
            print("警告： " + w)
        if len(self.ErrorList) == 0 and not self._malformed_xml_error:
            print("格式正确！")
        elif self._malformed_xml_error:
            print(f"输入疑似格式错误XML: {self._malformed_xml_error}")

    def getError(self):
        if len(self.ErrorList) != 0:
            return "\n".join(self.ErrorList)
        return ""

    def getWarnings(self):
        if self.WarningList:
            return "\n".join(self.WarningList)
        return ""

    def is_malformed_input(self):
        if self._malformed_xml_error:
            return True
        return False

    def remove_empty_lines(self, text):
        lines = text.split('\n')
        return [line for line in lines if line.strip()]

    def trim_string(self, s):
        start = 0
        end = len(s)
        for i, char in enumerate(s):
            if not char.isspace():
                start = i
                break
        for i in range(len(s) - 1, -1, -1):
            if not s[i].isspace():
                end = i + 1
                break
        return s[start:end]

    def extract_tag_left(self, line):
        tag_index = line.find("<")
        space_index = line.find(" ", tag_index)
        word = line[tag_index + 1:space_index]
        english_word = ''.join(word)
        if english_word.isalpha():
            return english_word
        return ""

    def extract_tag_right(self, line):
        tag_index = line.find("<")
        space_index = line.find(" ", tag_index)
        word = line[tag_index + 1:space_index]
        english_word = ''.join(filter(str.isalpha, word))
        if english_word.isalpha():
            return english_word
        return ""

    def extract_id(self, string):
        match = re.search(r'id=["\']([^"\']+)["\']', string)
        if match:
            return match.group(1)
        return ""

    def extract_condition(self, string):
        match = re.search(r'name=["\']([^"\']+)["\']', string)
        if match:
            return match.group(1)
        return ""

    def _extract_namespace_map(self, texts):
        namespace_map = {}
        if not texts:
            return namespace_map

        default_match = re.search(
            r'\bxmlns\s*=\s*["\']([^"\']+)["\']',
            texts
        )
        if default_match:
            namespace_map[""] = default_match.group(1)

        for prefix, uri in re.findall(
            r'\bxmlns:([A-Za-z_][\w.-]*)\s*=\s*["\']([^"\']+)["\']',
            texts
        ):
            namespace_map[prefix] = uri

        return namespace_map

    def ToTag(self, texts):
        legal_bpmn_tags = [
            'process', 'collaboration', 'participant', 'messageFlow', 'laneSet', 'lane',
            'startEvent', 'endEvent', 'task', 'userTask', 'serviceTask', 'manualTask', 'scriptTask', 'sendTask', 'receiveTask',
            'exclusiveGateway', 'parallelGateway', 'inclusiveGateway', 'eventBasedGateway', 'complexGateway',
            'sequenceFlow', 'incoming', 'outgoing', 'property', 'dataInputAssociation', 'dataOutputAssociation',
            'sourceRef', 'targetRef', 'dataObjectReference', 'dataObject', 'dataStoreReference', 'dataStore',
            'messageEventDefinition', 'timerEventDefinition', 'errorEventDefinition', 'signalEventDefinition', 'terminateEventDefinition',
            'definitions', 'association', 'subProcess', 'flowNodeRef', 'boundaryEvent', 'callActivity',
            'intermediateCatchEvent', 'intermediateThrowEvent', 'textAnnotation', 'group',
            'extensionElements', 'extension', 'documentation', 'auditing', 'monitoring',
            'categoryValue', 'category', 'conditionExpression', 'multiInstanceLoopCharacteristics',
            'standardLoopCharacteristics', 'loopCardinality', 'loopDataInputRef',
            'ioSpecification', 'dataInput', 'dataOutput', 'inputSet', 'outputSet',
            'inputSetRefs', 'outputSetRefs', 'resourceRole', 'potentialOwner', 'humanPerformer',
            'resourceAssignmentExpression', 'formalExpression', 'rendering',
            'conversation', 'conversationNode', 'conversationLink', 'participantAssociation',
            'messageFlowAssociation', 'correlationKey', 'correlationProperty', 'correlationPropertyBinding',
            'correlationSubscription', 'globalTask', 'globalUserTask', 'globalScriptTask', 'globalBusinessRuleTask',
            'globalManualTask', 'globalSendTask', 'globalReceiveTask',
            'interface', 'operation', 'message', 'input', 'output',
            'error', 'escalation', 'signal', 'itemDefinition', 'dataStoreReferenceRef',
        ]

        namespace_map = self._extract_namespace_map(texts)

        all_tags = re.finditer(r'<(/?)([\w:]+)([^>]*?)(/?)>', texts, re.DOTALL)

        line_starts = [0]
        for lmatch in re.finditer(r'\n', texts):
            line_starts.append(lmatch.end())

        def get_row(index):
            for i, start in enumerate(line_starts):
                if index < start:
                    return i
            return len(line_starts)

        matched_any = False

        for match in all_tags:
            matched_any = True
            is_closing_tag_marker = match.group(1)
            tag_name_raw = match.group(2)
            attrs_raw = match.group(3)
            self_close_marker = match.group(4)
            start_index = match.start()
            row = get_row(start_index)

            if tag_name_raw.count(':') > 1:
                self.ErrorList.append(
                    f"Invalid tag format '{tag_name_raw}' at row {row}: detected multiple colons. This might be a hallucination."
                )
                continue

            prefix = None
            local_name = tag_name_raw
            if ':' in tag_name_raw:
                parts = tag_name_raw.split(':', 1)
                prefix = parts[0]
                local_name = parts[1]

            if prefix is None:
                namespace_uri = namespace_map.get("", None)
            else:
                namespace_uri = namespace_map.get(prefix)

            if namespace_uri is None and prefix is not None:
                self.WarningList.append(
                    f"Unresolved namespace prefix '{prefix}' on tag '{tag_name_raw}' at row {row}. "
                    "Treat as unverifiable warning: ElementTree XML parser will validate namespace syntax."
                )

            is_bpmn_model = (namespace_uri == BPMN_MODEL_URI)
            is_bpmndi     = (namespace_uri == BPMNDI_URI)
            is_dc         = (namespace_uri == DC_URI)
            is_di         = (namespace_uri == DI_URI)
            is_schema     = (namespace_uri in {XSI_URI, XSD_URI})

            if not is_closing_tag_marker:
                if is_bpmn_model:
                    if local_name == "extensionElements":
                        pass
                    elif local_name not in legal_bpmn_tags:
                        self.WarningList.append(
                            f"Unknown or whitelist-missing BPMN tag "
                            f"'{tag_name_raw}' "
                            f"(namespace='{namespace_uri}', "
                            f"local name='{local_name}') "
                            f"at row {row}. "
                            "Treat as unverifiable warning: "
                            "do not auto-delete/rename unless "
                            "syntax/references are truly broken."
                        )
                elif is_bpmndi:
                    pass
                elif is_dc:
                    pass
                elif is_di:
                    pass
                elif is_schema:
                    pass
                else:
                    self.WarningList.append(
                        f"Unknown namespace/tag '{tag_name_raw}' "
                        f"(namespace='{namespace_uri}', "
                        f"local name='{local_name}') "
                        f"at row {row}. "
                        "Treat as unverifiable warning: "
                        "do not auto-delete/rename automatically."
                    )


            if namespace_uri in {BPMNDI_URI, DC_URI, DI_URI, XSI_URI, XSD_URI}:
                continue

            if self_close_marker == '/':
                continue

            is_closing = bool(is_closing_tag_marker)
            parentheses = ">" if is_closing else "<"

            content = local_name
            if 'task' in local_name.lower() or 'Event' in local_name or 'subProcess' in local_name:
                content = "activity"
            elif 'Gateway' in local_name:
                if 'exclusiveGateway' in local_name:
                    content = 'exclusiveGateway'
                elif 'parallelGateway' in local_name:
                    content = 'parallelGateway'
                elif 'inclusiveGateway' in local_name:
                    content = 'inclusiveGateway'

            idnum = self.extract_id(attrs_raw)
            condition = self.extract_condition(attrs_raw)
            spaces = 0

            self.line_tag.append(Tag(spaces, parentheses, content, condition, row, idnum))

        if not matched_any:
            self._malformed_xml_error = "No XML tags could be parsed from input (malformed XML?)."
            self.ErrorList.append("Malformed input: no XML tags parsed.")
            return

    def CheckPair(self, line_tag):
        stack1 = Stack()
        index = 0
        while index < len(line_tag):
            line = line_tag[index]
            if line.parentheses == "<":
                stack1.push(line)
                index = index + 1
            elif line.parentheses == ">":
                if stack1.isEmpty():
                    self.ErrorList.append(
                        "The '</" + line.content + ">' in line " + str(line.row) +
                        " is not matched, please consider adding the matched tag "
                        "in the appropriate position or removing the '</" +
                        line.content + ">' in line " + str(line.row) + "."
                    )
                    index = index + 1
                elif line.content == stack1.getTop().content:
                    stack1.pop()
                    index = index + 1
                else:
                    self.ErrorList.append(
                        "The tag '<" + stack1.getTop().content + ">' starting at line " +
                        str(stack1.getTop().row) + " is not closed properly "
                        "(encountered '</" + line.content + ">' at line " +
                        str(line.row) + ")."
                    )
                    stack1.pop()
            else:
                index = index + 1

        while not stack1.isEmpty():
            top = stack1.pop()
            self.ErrorList.append(
                "The tag '<" + top.content + ">' starting at line " +
                str(top.row) + " is never closed."
            )

    def CheckStructure(self,line_tag):
        return
