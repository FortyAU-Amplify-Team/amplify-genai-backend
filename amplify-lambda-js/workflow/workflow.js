import {chatWithDataStateless} from "../common/chatWithData.js";

import {getLogger} from "../common/logging.js";
import {StreamResultCollector, sendResultToStream, sendStatusEventToStream, findResultKey, endStream} from "../common/streams.js";


const logger = getLogger("workflow");


export const workflowSchema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "resultKeys": {
            "type": "array",
            "items":{"type":"string"}
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statusMessage": {
                        "type": "string"
                    },
                    "input": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "prompt": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ]
                    },
                    "reduce": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ]
                    },
                    "map": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ]
                    },
                    "outputTo": {
                        "type": "string"
                    }
                },
                "required": [
                    "statusMessage",
                    "input",
                    "outputTo"
                ],
                "additionalProperties": false,
                "oneOf": [
                    { "required": ["prompt"] },
                    { "required": ["reduce"] },
                    { "required": ["map"] }
                ]
            }
        }
    },
    "required": [
        "resultKey",
        "steps"
    ],
    "additionalProperties": false
};


function buildChatBody(step, body) {
    const customInstructions = step.customInstructions ? step.customInstructions : [];

    const history = body.messages ? body.messages : [];

    const updatedBody = {
        ...body, messages: [
            ...history,
            ...customInstructions,
            {role: "user", content: step.prompt || step.reduce || step.map },
        ]
    };

    return updatedBody;
}



const doPrompt = async ({
                            step,
                            body,
                            chatFn,
                            responseStream,
                            dataSources,
                            params
                        }) => {

    const updatedBody = buildChatBody(step, body);

    return chatWithDataStateless(
        params,
        chatFn,
        updatedBody,
        dataSources,
        responseStream);
}

const doMap = async ({
                         step,
                         dataSources,
                         body,
                         chatFn,
                         responseStream,
                         params
                     }) => {

    const updatedBody = buildChatBody(step, body);

    return chatWithDataStateless(
        params,
        chatFn,
        updatedBody,
        dataSources,
        responseStream);
}

const doReduce = async ({
                            step,
                            dataSources,
                            body,
                            chatFn,
                            responseStream,
                            params
                        }) => {

    const updatedBody = buildChatBody(step, body);

    const resultStream = new StreamResultCollector();

    const response = await chatWithDataStateless(
        params,
        chatFn,
        updatedBody,
        dataSources,
        resultStream);

    if (response) {
        return response;
    } else {
        const result = resultStream.result;
        const total = Object.keys(result).length;
        if (total > 0 && (total / 2) > 1) {
            const updatedStep = {...step, input: ["__lastResult"]};
            const updatedDataSources = resolveDataSources(
                updatedStep,
                {"__lastResult": result},
                []);

            await doReduce({
                step:updatedStep,
                dataSources:updatedDataSources,
                body,
                chatFn,
                responseStream,
                params
            });
        } else {
            const resultKey = findResultKey(result);
            if(resultKey){
                sendResultToStream(responseStream, result[resultKey]);
            }
            endStream(responseStream);
        }
    }


}


const getExecutor = (step) => {
    if (step.prompt) {
        return doPrompt;
    } else if (step.map) {
        return doMap;
    } else if (step.reduce) {
        return doReduce;
    }
}

const resolveDataSources = (step, workflowOutputs, externalDataSources) => {
    const dataSources = [];

    if (step.input) {
        for (const [index, inputName] of step.input.entries()) {
            if (inputName.startsWith("s3://")) {
                const dataSource = externalDataSources.find((ds) => ds.id === inputName);
                if (!dataSource) {
                    throw new Error("Data source not found: " + inputName);
                }
                dataSources.push(dataSource);
            } else {
                const dataSource = workflowOutputs[inputName];
                if (!dataSource) {
                    throw new Error("Data source not found: " + inputName);
                }
                dataSources.push({id: "obj://" + inputName, content: dataSource});
            }
        }
    }

    return dataSources;
}

export const executeWorkflow = async (
    {
        workflow,
        body,
        chatFn,
        dataSources,
        responseStream,
        params
    }) => {

    logger.debug("Starting workflow...");

    if (!workflow || !workflow.steps) {
        return {
            statusCode: 400,
            body: {error: "Bad request, invalid workflow."}
        };
    }

    const outputs = {};

    for (const [index, step] of workflow.steps.entries()) {

        logger.debug("Executing workflow step", {index, step});

        const executor = getExecutor(step);

        logger.debug("Building results collector...");
        const resultStream = new StreamResultCollector();

        if(step.statusMessage){
            sendStatusEventToStream(responseStream, step.statusMessage);
        }

        const resolvedDataSources = resolveDataSources(step, outputs, dataSources);

        const response = await executor({
            step,
            params,
            chatFn,
            body,
            dataSources: resolvedDataSources,
            responseStream: resultStream
        });

        logger.debug("Binding output of step to ", step.outputTo);
        logger.debug("Result", resultStream.result);

        outputs[step.outputTo] = resultStream.result;

        if (response) {
            // Error returned
            return {
                statusCode: 500,
                body: {error: "Error executing workflow at step:" + index}
            };
        }
    }

    const result = (workflow.resultKey) ? outputs[workflow.resultKey] : outputs;

    if(typeof result === 'object' && result !== null && !Array.isArray(result)){
        if(Object.keys(result).length === 2){
            const resultKey = findResultKey(result);
            if(resultKey){
                sendResultToStream(responseStream, result[resultKey]);
            }
        }
    }
    else {
        sendResultToStream(responseStream, result);
    }
    endStream(responseStream);
}

