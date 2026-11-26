using MFAAvalonia.Helper;
using MFAAvalonia.Helper.Converters;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.IO;

namespace MFAAvalonia.Configuration;

public sealed class InstanceConfiguration
{
    private readonly string _instanceId;
    private volatile Dictionary<string, object> _config;
    private readonly object _editLock = new();
    private readonly HashSet<string> _dirtyKeys = new();
    private volatile bool _isDeleted;

    internal static string InstancesDir => AppPaths.InstancesDirectory;

    public InstanceConfiguration(string instanceId)
    {
        _instanceId = instanceId;
        _config = LoadInstanceConfig();
    }

    /// <summary>
    /// 获取实例配置文件路径：config/instances/{id}.json
    /// </summary>
    public string GetConfigFilePath() =>
        Path.Combine(InstancesDir, $"{_instanceId}.json");

    public bool ConfigFileExists()
        => File.Exists(GetConfigFilePath());

    public bool HasLocalConfigData()
        => _config.Count > 0;

    /// <summary>
    /// 加载实例独立配置文件
    /// </summary>
    private Dictionary<string, object> LoadInstanceConfig()
    {
        var path = GetConfigFilePath();
        if (MFAAvalonia.Pcr.PcrAlphaPolicy.Enabled)
        {
            using var access = new JsonHelper.FileAccessScope(path);
            return ReadDocument(path).ToObject<Dictionary<string, object>>()!;
        }
        try
        {
            // Only an absent profile is empty. An unreadable profile must never
            // reach startup migrations or a later save as a writable default.
            return JObject.Parse(File.ReadAllText(GetConfigFilePath()))
                .ToObject<Dictionary<string, object>>()!;
        }
        catch (FileNotFoundException) { return new Dictionary<string, object>(); }
        catch (DirectoryNotFoundException) { return new Dictionary<string, object>(); }
    }

    /// <summary>
    /// 保存实例配置到独立文件
    /// </summary>
    public event Action<string>? PersistenceFailed;

    private static JObject ReadDocument(string path)
    {
        try
        {
            using var stream = JsonHelper.OpenReadSnapshot(path);
            using var reader = new StreamReader(stream);
            // Keep unrecognized settings, including date-shaped strings, as stored.
            using var json = new JsonTextReader(reader) { DateParseHandling = DateParseHandling.None };
            var document = JObject.Load(json);
            while (json.Read())
                if (json.TokenType != JsonToken.Comment) throw new JsonReaderException("实例配置包含多余内容。");
            return document;
        }
        catch (FileNotFoundException) { return new JObject(); }
        catch (DirectoryNotFoundException) { return new JObject(); }
    }

    private void ChangeValues(Dictionary<string, object> values, string? removedKey = null)
    {
        lock (_editLock)
        {
            if (_isDeleted) return;
            var candidate = new Dictionary<string, object>(_config);
            foreach (var (key, value) in values) candidate[key] = value;
            if (removedKey != null)
            {
                var removed = candidate.Remove(removedKey);
                if (!removed && !MFAAvalonia.Pcr.PcrAlphaPolicy.Enabled) return;
            }
            _config = candidate;
            if (MFAAvalonia.Pcr.PcrAlphaPolicy.Enabled)
            {
                _dirtyKeys.UnionWith(values.Keys);
                if (removedKey != null) _dirtyKeys.Add(removedKey);
            }
        }

        if (!MFAAvalonia.Pcr.PcrAlphaPolicy.Enabled)
        {
            JsonHelper.SaveJson(GetConfigFilePath(), _config,
                new MaaInterfaceSelectAdvancedConverter(false), new MaaInterfaceSelectOptionConverter(false));
            return;
        }

        try { PersistChanges(); }
        catch (Exception ex)
        {
            // A failed ordinary edit stays in memory and is retried on the next save.
            LoggerHelper.Error($"PCR 配置保存失败：{ex.Message}");
            PersistenceFailed?.Invoke(ex.Message);
        }
    }

    private void PersistChanges(string? strictKey = null, object? strictValue = null)
    {
        while (true)
        {
            Dictionary<string, object> original;
            Dictionary<string, object> changes = new();
            HashSet<string> keys;
            lock (_editLock)
            {
                if (_isDeleted)
                {
                    if (strictKey != null) throw new InvalidOperationException("实例配置已删除");
                    return;
                }
                original = _config;
                keys = new HashSet<string>(_dirtyKeys);
                foreach (var key in keys)
                    if (original.TryGetValue(key, out var value)) changes.Add(key, value);
                if (strictKey != null)
                {
                    keys.Add(strictKey);
                    changes[strictKey] = strictValue!;
                }
                if (keys.Count == 0) return;
            }

            // Native option values can require the UI dispatcher to serialize.
            // No state or file lock may be held while waiting for that dispatcher.
            var patch = JsonHelper.SerializeForAtomicSave(changes,
                new MaaInterfaceSelectAdvancedConverter(false), new MaaInterfaceSelectOptionConverter(false));
            using var patchReader = new JsonTextReader(new StringReader(patch)) { DateParseHandling = DateParseHandling.None };
            var fields = JObject.Load(patchReader);
            lock (_editLock)
            {
                if (!ReferenceEquals(original, _config) || _isDeleted) continue;
                var path = GetConfigFilePath();
                using var access = new JsonHelper.FileAccessScope(path);
                var document = ReadDocument(path);
                bool changed = false;
                foreach (var key in keys)
                {
                    if (fields.TryGetValue(key, out var token))
                    {
                        document[key] = token;
                        changed = true;
                    }
                    else changed |= document.Remove(key);
                }
                if (changed) JsonHelper.SaveJsonTextAtomicallyOrThrow(path, document.ToString(Formatting.Indented));
                var committed = new Dictionary<string, object>(original);
                if (strictKey != null) committed[strictKey] = strictValue!;
                _config = committed;
                _dirtyKeys.Clear();
                return;
            }
        }
    }

    private MFAConfiguration GlobalConfig => ConfigurationManager.Current;

    private bool ShouldPersistFallbackValue()
    {
        if (_isDeleted)
            return false;

        if (_instanceId != "default")
            return true;

        return File.Exists(GetConfigFilePath());
    }

    private void PersistFallbackValue(string key, object? value)
    {
        if (!ShouldPersistFallbackValue())
            return;

        SetValue(key, value);
    }

    public bool ContainsKey(string key)
        => _config.ContainsKey(key) || GlobalConfig.ContainsKey(key);

    public void SetValue(string key, object? value)
    {
        if (value == null || _isDeleted) return;
        ChangeValues(new Dictionary<string, object> { [key] = value });
    }

    /// <summary>Persist a candidate before exposing it to in-memory reads.</summary>
    public void SetValueAndPersist(string key, object value)
    {
        if (_isDeleted) throw new InvalidOperationException("实例配置已删除");
        ArgumentNullException.ThrowIfNull(value);
        if (MFAAvalonia.Pcr.PcrAlphaPolicy.Enabled)
        {
            PersistChanges(key, value);
            return;
        }
        var candidate = new Dictionary<string, object>(_config) { [key] = value };
        JsonHelper.SaveJsonAtomicallyOrThrow(
            GetConfigFilePath(), candidate,
            new MaaInterfaceSelectAdvancedConverter(false),
            new MaaInterfaceSelectOptionConverter(false));
        _config = candidate;
    }

    public void RemoveValue(string key)
    {
        if (_isDeleted) return;
        ChangeValues(new Dictionary<string, object>(), key);
    }

    public T GetValue<T>(string key, T defaultValue)
    {
        var policy = MFAAvalonia.Pcr.PcrAlphaPolicy.Value(key);
        if (policy != null) return (T)Convert.ChangeType(policy, typeof(T));
        // 优先从实例独立配置读取
        if (_config.TryGetValue(key, out var data))
        {
            var result = ConvertValue<T>(data);
            if (result != null) return result;
        }

        // 回退：从全局 config.json 的旧 scoped key 读取（兼容过渡）
        var scopedKey = $"Instance.{_instanceId}.{key}";
        if (GlobalConfig.ContainsKey(scopedKey))
        {
            var value = GlobalConfig.GetValue<T>(scopedKey, defaultValue);
            // 迁移到实例文件；临时 default 实例不落盘，避免重建 default.json
            PersistFallbackValue(key, value);
            return value;
        }

        // 回退：从 default 实例的 scoped key 读取
        if (_instanceId != "default")
        {
            var defaultScopedKey = $"Instance.default.{key}";
            if (GlobalConfig.ContainsKey(defaultScopedKey))
            {
                var value = GlobalConfig.GetValue<T>(defaultScopedKey, defaultValue);
                PersistFallbackValue(key, value);
                return value;
            }
        }

        // 回退：从全局 config.json 的无前缀 key 读取（最早版本兼容）
        if (GlobalConfig.ContainsKey(key))
        {
            var value = GlobalConfig.GetValue<T>(key, defaultValue);
            PersistFallbackValue(key, value);
            return value;
        }

        return defaultValue;
    }

    public T GetValue<T>(string key, T defaultValue, List<T> whitelist)
    {
        var value = GetValue(key, defaultValue);
        return whitelist.Contains(value) ? value : defaultValue;
    }

    public T GetValue<T>(string key, T defaultValue, Dictionary<object, T> options)
    {
        if (_config.TryGetValue(key, out var data))
        {
            if (options != null && options.TryGetValue(data, out var result))
                return result;
            var converted = ConvertValue<T>(data);
            if (converted != null) return converted;
        }

        // 回退到全局配置
        var scopedKey = $"Instance.{_instanceId}.{key}";
        if (GlobalConfig.ContainsKey(scopedKey))
        {
            var value = GlobalConfig.GetValue<T>(scopedKey, defaultValue, options);
            PersistFallbackValue(key, value);
            return value;
        }

        if (_instanceId != "default")
        {
            var defaultScopedKey = $"Instance.default.{key}";
            if (GlobalConfig.ContainsKey(defaultScopedKey))
            {
                var value = GlobalConfig.GetValue<T>(defaultScopedKey, defaultValue, options);
                PersistFallbackValue(key, value);
                return value;
            }
        }

        if (GlobalConfig.ContainsKey(key))
        {
            var value = GlobalConfig.GetValue<T>(key, defaultValue, options);
            PersistFallbackValue(key, value);
            return value;
        }

        return defaultValue;
    }

    public T GetValue<T>(string key, T defaultValue, T? noValue = default, params JsonConverter[] valueConverters)
    {
        if (_config.TryGetValue(key, out var data))
        {
            try
            {
                var settings = new JsonSerializerSettings();
                foreach (var converter in valueConverters)
                    settings.Converters.Add(converter);
                var result = JsonConvert.DeserializeObject<T>(JsonConvert.SerializeObject(data), settings) ?? defaultValue;
                if (result != null && !result.Equals(noValue))
                    return result;
            }
            catch (Exception e)
            {
                LoggerHelper.Error($"类型转换失败: {e.Message}");
            }
        }

        // 回退到全局配置
        var scopedKey = $"Instance.{_instanceId}.{key}";
        if (GlobalConfig.ContainsKey(scopedKey))
        {
            var value = GlobalConfig.GetValue<T>(scopedKey, defaultValue, noValue, valueConverters);
            PersistFallbackValue(key, value);
            return value;
        }

        if (_instanceId != "default")
        {
            var defaultScopedKey = $"Instance.default.{key}";
            if (GlobalConfig.ContainsKey(defaultScopedKey))
            {
                var value = GlobalConfig.GetValue<T>(defaultScopedKey, defaultValue, noValue, valueConverters);
                PersistFallbackValue(key, value);
                return value;
            }
        }

        if (GlobalConfig.ContainsKey(key))
        {
            var value = GlobalConfig.GetValue<T>(key, defaultValue, noValue, valueConverters);
            PersistFallbackValue(key, value);
            return value;
        }

        return defaultValue;
    }

    public T GetValue<T>(string key, T defaultValue, List<T>? noValue = null, params JsonConverter[] valueConverters)
    {
        if (_config.TryGetValue(key, out var data))
        {
            try
            {
                var settings = new JsonSerializerSettings();
                foreach (var converter in valueConverters)
                    settings.Converters.Add(converter);
                var result = JsonConvert.DeserializeObject<T>(JsonConvert.SerializeObject(data), settings) ?? defaultValue;
                if (noValue == null || !noValue.Contains(result))
                    return result;
            }
            catch (Exception e)
            {
                LoggerHelper.Error($"类型转换失败: {e.Message}");
            }
        }

        // 回退到全局配置
        var scopedKey = $"Instance.{_instanceId}.{key}";
        if (GlobalConfig.ContainsKey(scopedKey))
        {
            var value = GlobalConfig.GetValue<T>(scopedKey, defaultValue, noValue, valueConverters);
            PersistFallbackValue(key, value);
            return value;
        }

        if (_instanceId != "default")
        {
            var defaultScopedKey = $"Instance.default.{key}";
            if (GlobalConfig.ContainsKey(defaultScopedKey))
            {
                var value = GlobalConfig.GetValue<T>(defaultScopedKey, defaultValue, noValue, valueConverters);
                PersistFallbackValue(key, value);
                return value;
            }
        }

        if (GlobalConfig.ContainsKey(key))
        {
            var value = GlobalConfig.GetValue<T>(key, defaultValue, noValue, valueConverters);
            PersistFallbackValue(key, value);
            return value;
        }

        return defaultValue;
    }

    public bool TryGetValue<T>(string key, out T output, params JsonConverter[] valueConverters)
    {
        if (_config.TryGetValue(key, out var data))
        {
            try
            {
                var settings = new JsonSerializerSettings();
                foreach (var converter in valueConverters)
                    settings.Converters.Add(converter);
                output = JsonConvert.DeserializeObject<T>(JsonConvert.SerializeObject(data), settings) ?? default!;
                return true;
            }
            catch
            {
                // fall through
            }
        }

        // 回退到全局配置
        var scopedKey = $"Instance.{_instanceId}.{key}";
        if (GlobalConfig.TryGetValue(scopedKey, out output, valueConverters))
        {
            PersistFallbackValue(key, output);
            return true;
        }

        if (_instanceId != "default")
        {
            var defaultScopedKey = $"Instance.default.{key}";
            if (GlobalConfig.TryGetValue(defaultScopedKey, out output, valueConverters))
            {
                PersistFallbackValue(key, output);
                return true;
            }
        }

        if (GlobalConfig.TryGetValue(key, out output, valueConverters))
        {
            PersistFallbackValue(key, output);
            return true;
        }

        output = default!;
        return false;
    }

    /// <summary>
    /// 从实例配置文件中快速读取指定 key 的字符串值（不创建完整实例）
    /// </summary>
    public static string ReadValueFromFile(string instanceId, string key, string defaultValue = "")
    {
        var filePath = Path.Combine(InstancesDir, $"{instanceId}.json");
        var pcr = MFAAvalonia.Pcr.PcrAlphaPolicy.Enabled;
        if (!pcr && !File.Exists(filePath)) return defaultValue;

        try
        {
            using var access = pcr ? new JsonHelper.FileAccessScope(filePath) : null;
            var data = pcr
                ? ReadDocument(filePath).ToObject<Dictionary<string, object>>()!
                : JsonHelper.LoadJson(filePath, new Dictionary<string, object>());
            if (data.TryGetValue(key, out var value))
            {
                if (value is string str)
                    return str;

                if (value is JValue jValue)
                    return jValue.ToString();

                if (value != null)
                    return Convert.ToString(value) ?? defaultValue;
            }
        }
        catch (Exception ex)
        {
            LoggerHelper.Error($"[实例加载] 读取实例名称失败: {filePath}", ex);
        }

        return defaultValue;
    }

    /// <summary>
    /// 从磁盘重新加载配置（迁移后调用，刷新内存中的过期数据）
    /// </summary>
    public void ReloadFromDisk()
    {
        lock (_editLock)
        {
            _config = LoadInstanceConfig();
            _dirtyKeys.Clear();
        }
    }

    /// <summary>
    /// 将当前实例的配置复制到新实例的配置文件（排除实例名称），
    /// 需在新实例创建前调用，确保构造函数能读到完整配置
    /// </summary>
    public void CopyToNewInstance(string targetInstanceId)
    {
        if (!Directory.Exists(InstancesDir))
            Directory.CreateDirectory(InstancesDir);

        var data = new Dictionary<string, object>(_config);
        data.Remove(ConfigurationKeys.InstanceName);

        JsonHelper.SaveJson(
            Path.Combine(InstancesDir, $"{targetInstanceId}.json"),
            data,
            new MaaInterfaceSelectAdvancedConverter(false),
            new MaaInterfaceSelectOptionConverter(false));
    }

    /// <summary>
    /// 批量设置配置（用于迁移，只保存一次）
    /// </summary>
    public void SetValues(Dictionary<string, object> values)
    {
        ChangeValues(values);
    }

    /// <summary>
    /// 删除实例配置文件
    /// </summary>
    public void DeleteConfigFile()
    {
        lock (_editLock)
        {
            _isDeleted = true;
            var filePath = GetConfigFilePath();
            using var access = MFAAvalonia.Pcr.PcrAlphaPolicy.Enabled ? new JsonHelper.FileAccessScope(filePath) : null;
            if (File.Exists(filePath)) File.Delete(filePath);
        }
    }

    /// <summary>
    /// 反序列化时使用的自定义转换器（与 SaveInstanceConfig 保持一致，确保读写对称）
    /// </summary>
    private static readonly JsonSerializerSettings DeserializeSettings = new()
    {
        Converters =
        {
            new MaaInterfaceSelectAdvancedConverter(false),
            new MaaInterfaceSelectOptionConverter(false),
        }
    };

    private static T? ConvertValue<T>(object data)
    {
        try
        {
            if (data is long longValue && typeof(T) == typeof(int))
                return (T)(object)Convert.ToInt32(longValue);

            if (data is T t)
                return t;

            if (data is JArray jArray)
                return JsonConvert.DeserializeObject<T>(jArray.ToString(), DeserializeSettings);

            if (data is JObject jObject)
                return JsonConvert.DeserializeObject<T>(jObject.ToString(), DeserializeSettings);
        }
        catch (Exception e)
        {
            LoggerHelper.Error("在进行类型转换时发生错误!", e);
        }

        return default;
    }
}
