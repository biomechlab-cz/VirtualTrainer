from cstruct import MemCStruct

# NEEDS TO BE THE EXACT SAME AS THE C STRUCT DEFINITION

class SocketDataStructure(MemCStruct):
    __def__ = """
        #define CHUNK_SEND_SIZE 100
        #define CHUNK_SEND_SIZE_SECONDARY CHUNK_SEND_SIZE / 10
        struct SocketSendStruct {
            uint8_t isInitialData;
            uint8_t batteryLevel;
            union DataUnion {
                struct DataStruct {
                    int32_t emg_data_arr[CHUNK_SEND_SIZE];
                    int16_t compass_x[CHUNK_SEND_SIZE_SECONDARY];
                    int16_t compass_y[CHUNK_SEND_SIZE_SECONDARY];
                    int16_t compass_z[CHUNK_SEND_SIZE_SECONDARY];
                    int16_t compass_t[CHUNK_SEND_SIZE_SECONDARY];
                    int16_t imu_acc_x[CHUNK_SEND_SIZE_SECONDARY];
                    int16_t imu_acc_y[CHUNK_SEND_SIZE_SECONDARY];
                    int16_t imu_acc_z[CHUNK_SEND_SIZE_SECONDARY];
                    int16_t imu_gyro_x[CHUNK_SEND_SIZE_SECONDARY];
                    int16_t imu_gyro_y[CHUNK_SEND_SIZE_SECONDARY];
                    int16_t imu_gyro_z[CHUNK_SEND_SIZE_SECONDARY];
                    uint64_t time[CHUNK_SEND_SIZE_SECONDARY];
                } data;
                 struct InitialDataStructure{
                    char participant[50];
                    char position[50];
                    uint16_t device_id;
                } initialData;
            } unionData;
        };
    """