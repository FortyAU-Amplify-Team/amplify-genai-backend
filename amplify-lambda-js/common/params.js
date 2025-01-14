import {chat} from "../azure/openai.js";
import {chatAnthropic} from "../bedrock/anthropic.js";
import { chatBedrock } from "../bedrock/bedrock.js";
import {chatMistral} from "../bedrock/mistral.js";
import {getLLMConfig} from "../common/secrets.js";

export const getRequestId = (params) => {
    return params.requestId;
}

export const getModel = (params) => {
    return params.model;
}

export const getCheapestModel = (params) => {
    return params.cheapestModel ?? (params.options.cheapestModel ?? getModel(params));
}

export const getAdvancedModel = (params) => {
    return params.advancedModel ?? (params.options.advancedModel ?? getModel(params));
}

export const setModel = (params, model) => {
    return {...params, model};
}

export const getOptions = (params) => {
    return params.options;
}

export const setUser = (params, user) => {
    return {...params, account:{user}};
}

export const getUser = (params) => {
    return params.account.user;
}

export const getAccessToken = (params) => {
    return params.account.accessToken;
}

export const getAccountId = (params) => {
    return params.account.accountId;
}

export const getMaxTokens = (params) => {
    return params.options.maxTokens;
}

export const getChatFn = (model, body, writable, context) => {

    if (model.id.includes("gpt") || model.id.includes("o1") ) {
        return chat(getLLMConfig, body, writable, context);
    } else if (model.provider === 'Bedrock') {
        return chatBedrock(body, writable, context);
    } else {
        console.log(`Error: Model ${model} does not have a corresponding chatFn`)
        return null;
    }
}