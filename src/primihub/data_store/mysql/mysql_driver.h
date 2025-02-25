/*
 Copyright 2022 Primihub

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      https://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
 */

#ifndef SRC_PRIMIHUB_DATA_STORE_MYSQL_MYSQL_DRIVER_H_
#define SRC_PRIMIHUB_DATA_STORE_MYSQL_MYSQL_DRIVER_H_

#include "src/primihub/data_store/dataset.h"
#include "src/primihub/data_store/driver.h"
#include "src/primihub/util/arrow_wrapper_util.h"
#include <iomanip>
#include <map>
#include <glog/logging.h>
#include <arrow/api.h>
#include <mysql/mysql.h>

namespace primihub {
class MySQLDriver;
auto conn_dctor = [](MYSQL* conn) {
  if (conn != nullptr) {
    VLOG(5) << "begin to close conn";
    mysql_close(conn);
    mysql_library_end();
  }
};

auto conn_threadsafe_dctor = [](MYSQL* conn) {
  if (conn != nullptr) {
    VLOG(5) << "begin to close conn";
    mysql_close(conn);
  }
  mysql_thread_end();
};

struct MySQLAccessInfo : public DataSetAccessInfo {
  MySQLAccessInfo() = default;
  MySQLAccessInfo(const std::string& ip, uint32_t port, const std::string& user_name,
      const std::string& password, const std::string& database,
      const std::string& db_name, const std::string& table_name,
      const std::vector<std::string>& query_colums)
      : ip_(ip), port_(port), password_(password),
      database_(database), user_name_(user_name),
      db_name_(db_name), table_name_(table_name) {
    if (!query_colums.empty()) {
      for (const auto& col : query_colums) {
        query_colums_.push_back(col);
      }
    }
  }
  std::string toString() override;
  retcode ParseFromJsonImpl(const nlohmann::json& access_info) override;
  retcode ParseFromYamlConfigImpl(const YAML::Node& meta_info) override;
  retcode ParseFromMetaInfoImpl(const DatasetMetaInfo& meta_info) override;

 public:
  std::string ip_;
  uint32_t port_{0};
  std::string user_name_;
  std::string password_;
  std::string database_;
  std::string db_name_;
  std::string table_name_;
  std::vector<std::string> query_colums_;
};

class MySQLCursor : public Cursor {
 public:
    MySQLCursor(const std::string& sql, std::shared_ptr<MySQLDriver> driver);
    MySQLCursor(const std::string& sql,
                const std::vector<int>& selected_column_index,
                std::shared_ptr<MySQLDriver> driver);
    ~MySQLCursor();
    std::shared_ptr<primihub::Dataset> readMeta() override;
    std::shared_ptr<primihub::Dataset> read() override;
    std::shared_ptr<primihub::Dataset> read(int64_t offset, int64_t limit);
    int write(std::shared_ptr<primihub::Dataset> dataset) override;
    void close() override;

 protected:
    auto getDBConnector(std::unique_ptr<DataSetAccessInfo>& access_info) ->
        std::unique_ptr<MYSQL, decltype(conn_threadsafe_dctor)>;

    retcode fetchData(const std::string& query_sql,
                      std::vector<std::shared_ptr<arrow::Array>>* data_arr);
    std::shared_ptr<arrow::Schema> makeArrowSchema();

 private:
    std::string sql_;
    size_t offset{0};
    std::shared_ptr<MySQLDriver> driver_{nullptr};
};

class MySQLDriver : public DataDriver, public std::enable_shared_from_this<MySQLDriver> {
 public:
    explicit MySQLDriver(const std::string& nodelet_addr);
    MySQLDriver(const std::string &nodelet_addr, std::unique_ptr<DataSetAccessInfo> access_info);
    ~MySQLDriver();
    std::unique_ptr<Cursor> read() override;
    std::unique_ptr<Cursor> read(const std::string& conn_str) override;
    std::unique_ptr<Cursor> GetCursor() override;
    std::unique_ptr<Cursor> GetCursor(const std::vector<int>& col_index) override;
    std::unique_ptr<Cursor> initCursor(const std::string& conn_str) override;
    std::string getDataURL() const override;
    // write data to specifiy db table
    int write(std::shared_ptr<arrow::Table> table, const std::string& table_name);
    std::map<std::string, std::string>& tableSchema() {
        return table_schema_;
    }
    std::vector<std::string>& tableColums() {
        return table_cols_;
    }
    MYSQL* getDBConnector() { return db_connector_.get(); }

 protected:
    retcode initMySqlLib();
    retcode releaseMySqlLib();
    std::string getMySqlError();
    bool isConnected();
    bool reConnect();
    retcode connect(MySQLAccessInfo* access_info);
    retcode executeQuery(const std::string& sql_query);
    retcode getTableSchema(const std::string& db_name, const std::string& table_name);
    std::string buildQuerySQL(MySQLAccessInfo* access_info);
    void setDriverType();
    std::unique_ptr<Cursor> MakeCursor(std::vector<int> col_index);
    std::string BuildQuerySQL(const MySQLAccessInfo& access_info,
                              const std::vector<int>& col_index,
                              std::vector<std::string>* colum_names);

 private:
    std::string conn_info_;
    std::atomic_bool connected{false};
    std::unique_ptr<MYSQL, decltype(conn_dctor)> db_connector_{nullptr, conn_dctor};
    std::vector<std::string> table_cols_;
    std::map<std::string, std::string> table_schema_;   // mysql schema
    int32_t connect_timeout_ms{3000};
};

} // namespace primihub

#endif // SRC_PRIMIHUB_DATA_STORE_MYSQL_MYSQL_DRIVER_H_
